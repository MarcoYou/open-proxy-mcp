"""screener 카드 — 중복처럼 읽히는 카드·빠진 구분·헷갈리는 칸 이름. 네트워크 0.

배경(10-22 사용자 유형 시험):
- 신한지주 배당이 접수번호 20261021800514·20261021800503 두 카드로 나왔다. 원문을 열어 보니 정정 짝이 아니라
  **신한은행 중간배당 한 결의의 두 서식**(현금ㆍ현물배당결정 / 주주명부폐쇄(기준일)결정)이었고, 둘 다
  「자회사의 주요경영사항」이었다. 게다가 두 카드에 신한지주 **자신의** DPS 1,480원·기준일 2026-12-31 이 똑같이 붙었다
  (신한은행 공시는 630.67원·2026-11-05) — 배당·실적 파서가 접수번호를 보지 않고 회사·날짜로만 공시를 찾기 때문이다.
- HD한국조선해양 잠정실적 두 건(20261019800441 자회사 공시 / 800444 자신의 공시)은 중복이 아니라 서로 다른 공시다.
- details 를 안 켠 카드에는 금액이 없고, 시총 「42.0조」가 계약금액으로 오독됐다. 자기주식 카드에는 취득·소각 구분이 없었다.
"""
import asyncio

import pytest

import open_proxy_mcp.services.screener as S
from open_proxy_mcp.tools.screener import _render_digest

SHINHAN = dict(corp="00382199", name="신한지주", stock="055550")
DIV_DECISION = "현금ㆍ현물배당결정(자회사의 주요경영사항)              "
DIV_RECORD = "현금ㆍ현물배당을위한주주명부폐쇄(기준일)결정(자회사의 주요경영사항)              "


def _it(rcept_no: str, report_nm: str, *, corp: str = SHINHAN["corp"], name: str = SHINHAN["name"],
        stock: str = SHINHAN["stock"], dt: str = "20261021") -> dict:
    return {"rcept_no": rcept_no, "report_nm": report_nm, "corp_code": corp, "corp_name": name,
            "stock_code": stock, "corp_cls": "Y", "flr_nm": name, "rcept_dt": dt}


class _FakeClient:
    def api_call_snapshot(self):
        return 0


def _run(items: list[dict], *, scan_code: str = "I001", types: str = "dividend",
         start: str = "20261021", end: str = "20261021", **kw):
    async def _fake_scan(client, code, bgn, end_, mx):
        return S._scan_result(items if code == scan_code else [], len(items), 1, 1)

    orig = S._scan_code, S.get_dart_client, S._krx_mktcap_map
    S._scan_code = _fake_scan
    S.get_dart_client = lambda: _FakeClient()
    S._krx_mktcap_map = lambda codes, dd: {}
    try:
        return asyncio.run(S.build_screener_payload(
            types=types, period="custom", custom_start=start, custom_end=end, universe="all", **kw))
    finally:
        S._scan_code, S.get_dart_client, S._krx_mktcap_map = orig


# ── 같은 결의의 배당 결정·기준일 공시 ──────────────────────────────────────────

def test_dividend_decision_and_record_date_filings_of_one_resolution_become_one_card():
    """신한지주 실사례 — 카드 한 장, 기준일 공시는 지우지 않고 companions 에 남는다."""
    p = _run([_it("20261021800514", DIV_DECISION), _it("20261021800503", DIV_RECORD)], details=False)
    assert p["counts"]["matched"] == 1, "같은 결의의 두 서식이 카드 두 장으로 나왔다"
    assert p["counts"]["companion_merged"] == 1
    card = p["hits"][0]
    assert card["rcept_no"] == "20261021800514", "금액이 있는 결정 공시가 주 카드다"
    assert [c["rcept_no"] for c in card["companions"]] == ["20261021800503"]
    assert card["companions"][0]["dart_url"].endswith("20261021800503")


def test_the_companion_rule_does_not_guess_when_it_cannot_tell_which_is_whose():
    """list.json 은 두 공시가 같은 결의인지 말해 주지 않는다 — 결정이 둘이면(보통주·종류주 따로 등) 묶지 않는다."""
    p = _run([_it("20261021800514", DIV_DECISION), _it("20261021800515", DIV_DECISION),
              _it("20261021800503", DIV_RECORD)], details=False)
    assert p["counts"]["matched"] == 3
    assert p["counts"]["companion_merged"] == 0
    assert not any(h.get("companions") for h in p["hits"])


def test_filings_of_different_days_or_different_filers_are_not_merged():
    p = _run([_it("20261020800514", DIV_DECISION, dt="20261020"), _it("20261021800503", DIV_RECORD)],
             start="20261020", details=False)
    assert p["counts"]["matched"] == 2, "접수일이 다르면 같은 결의라고 말할 근거가 없다"
    other = _run([_it("20261021800514", DIV_DECISION),
                  _it("20261021800503", DIV_RECORD, corp="00000001", name="다른회사", stock="000001")],
                 details=False)
    assert other["counts"]["matched"] == 2


def test_own_filing_and_subsidiary_matter_are_not_merged_with_each_other():
    """모회사 자신의 배당 결정과 자회사 소식은 별개 결의다."""
    own = "현금ㆍ현물배당결정"
    p = _run([_it("20261021800514", own), _it("20261021800503", DIV_RECORD)], details=False)
    assert p["counts"]["matched"] == 2


def test_a_correction_is_never_folded_into_the_companion_card():
    p = _run([_it("20261021800514", DIV_DECISION),
              _it("20261021800516", "[기재정정]" + DIV_RECORD)], details=False)
    assert p["counts"]["matched"] == 2


# ── 자회사 공시 구분 ────────────────────────────────────────────────────────────

def test_subsidiary_filings_are_marked_and_kept_apart_from_the_parents_own():
    """HD한국조선해양 — 자회사 공시(800441)와 자신의 공시(800444)는 중복이 아니다."""
    items = [_it("20261019800441", "영업(잠정)실적(공정공시)(자회사의 주요경영사항)", corp="00164830",
                 name="HD한국조선해양", stock="009540", dt="20261019"),
             _it("20261019800444", "영업(잠정)실적(공정공시)", corp="00164830",
                 name="HD한국조선해양", stock="009540", dt="20261019")]
    p = _run(items, scan_code="I002", types="earnings", start="20261019", end="20261019", details=False)
    by = {h["rcept_no"]: h for h in p["hits"]}
    assert p["counts"]["matched"] == 2
    assert by["20261019800441"].get("subsidiary_matter") is True
    assert "subsidiary_matter" not in by["20261019800444"]
    md = _render_digest(p)
    assert md.count("자회사 공시") == 1, "자회사 공시 카드에만 표시한다"


def test_a_subsidiary_correction_replaces_the_subsidiary_original_not_the_parents_own():
    """키가 같으면 원본 후보가 둘이라 정정 짝을 못 찾거나 엉뚱한 쪽을 대체한다."""
    sub_nm = "영업(잠정)실적(공정공시)(자회사의 주요경영사항)"
    kw = dict(corp="00164830", name="HD한국조선해양", stock="009540", dt="20261019")
    items = [_it("20261019800441", sub_nm, **kw), _it("20261019800444", "영업(잠정)실적(공정공시)", **kw),
             _it("20261019800460", "[기재정정]" + sub_nm, **kw)]
    p = _run(items, scan_code="I002", types="earnings", start="20261019", end="20261019", details=False)
    assert p["counts"]["matched"] == 2 and p["counts"]["deduped_away"] == 1
    sub = next(h for h in p["hits"] if h.get("subsidiary_matter"))
    assert sub["rcept_no"] == "20261019800460" and sub["supersedes_rcept_no"] == "20261019800441"
    assert not any(h.get("ambiguous_correction") for h in p["hits"])


@pytest.mark.parametrize("kind", ["dividend", "earnings"])
def test_subsidiary_card_does_not_borrow_the_parents_numbers(kind, monkeypatch):
    """배당·실적 파서는 접수번호를 보지 않는다 — 열면 모회사 값이 자회사 카드에 붙는다. 열지 않고 밝힌다."""
    def _boom():
        raise AssertionError("자회사 공시 카드를 위해 문서를 열었다")
    monkeypatch.setattr(S, "get_dart_client", _boom)
    res = asyncio.run(S._fetch_detail(
        {"_detail_kind": kind, "subsidiary_matter": True, "rcept_no": "20261021800514",
         "stock_code": "055550", "filed_at": "2026-10-21", "title": "x"}, {"calls": 0}))
    assert res["detail_status"] == "no_data" and res["fields"] == {}
    assert "자회사" in res["note"]


def test_a_parents_own_dividend_card_still_opens_its_document(monkeypatch):
    """가드는 자회사 공시에만 건다 — 모회사 자신의 카드는 그대로 연다."""
    import open_proxy_mcp.services.dividend as D

    async def _payload(company, **kw):
        return {"status": "ok", "data": {"summary": {"cash_dps": 1480, "stlm_dt": "2026-12-31"}}}

    monkeypatch.setattr(D, "build_dividend_payload", _payload)
    monkeypatch.setattr(S, "get_dart_client", lambda: _FakeClient())
    res = asyncio.run(S._fetch_detail(
        {"_detail_kind": "dividend", "rcept_no": "20261021800001", "stock_code": "055550",
         "filed_at": "2026-10-21", "title": "x"}, {"calls": 0}))
    assert res["detail_status"] == "parsed" and res["fields"]["dps_won"] == 1480


# ── 자기주식 구분 ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("title,label", [
    ("주요사항보고서(자기주식취득결정)", "취득"),
    ("주요사항보고서(자기주식처분결정)", "처분"),
    ("주요사항보고서(자기주식소각결정)", "소각"),
    ("주요사항보고서(자기주식취득신탁계약체결결정)", "신탁 체결"),
    ("주요사항보고서(자기주식취득신탁계약해지결정)", "신탁 해지"),
])
def test_treasury_card_says_which_kind_it_is(title, label):
    p = _run([_it("20261021800001", title, corp="00000002", name="삼성E&A", stock="028050")],
             scan_code="B001", types="treasury", details=False)
    card = p["hits"][0]
    assert card["treasury_action"] == label
    assert f"자기주식 {label}" in _render_digest(p)


def test_only_treasury_cards_carry_the_treasury_action():
    p = _run([_it("20261021800001", "현금ㆍ현물배당결정")], details=False)
    assert "treasury_action" not in p["hits"][0]


# ── 칸 이름 ─────────────────────────────────────────────────────────────────────

def _card(**kw) -> dict:
    base = {"rcept_no": "20261021800001", "corp_name": "테스트", "stock_code": "000001",
            "type": {"code": "order", "label": "수주(단일판매·공급계약)"}, "stage": "결정(예정)",
            "mktcap_won": 42_0000_0000_0000, "title": "단일판매ㆍ공급계약 체결", "dart_url": "",
            "detail_status": "parsed", "detail_fields": {"amount_won": 5314_0000_0000}}
    base.update(kw)
    return base


def _payload(hits: list[dict], **counts) -> dict:
    return {"status": "ok", "as_of": "2026-10-22T09:00:00",
            "period": {"bgn_de": "20261021", "end_de": "20261021", "days": 0},
            "universe": {"label": "지정 1종목"},
            "counts": {"scanned": 10, "matched": len(hits), "hits": len(hits), "returned": len(hits), **counts},
            "paging": {"offset": 0, "page_size": 200, "matched": len(hits), "returned": len(hits),
                       "has_more": False},
            "coverage": [], "hits": hits, "warnings": []}


def test_market_cap_and_contract_amount_carry_their_own_names():
    """「42.0조」는 시총인데 계약금액으로 오독됐다 — 두 칸 모두 이름표를 단다."""
    out = _render_digest(_payload([_card()]))
    assert "시총 42.0조" in out
    assert "계약금액 5,314억원" in out
    assert "· 42.0조 ·" not in out, "이름 없는 시총이 남았다"


def test_missing_market_cap_keeps_the_label():
    assert "시총 –" in _render_digest(_payload([_card(mktcap_won=None)]))


def test_companion_and_subsidiary_lines_render():
    card = _card(type={"code": "dividend", "label": "배당"}, subsidiary_matter=True, detail_status="no_data",
                 detail_fields={}, detail_note="자회사의 주요경영사항 공시 — 모회사 값과 섞이지 않게 숫자를 싣지 않았다",
                 companions=[{"rcept_no": "20261021800503", "subtype": "기준일", "title": "t",
                              "filed_at": "2026-10-21", "dart_url": "https://dart.example/20261021800503"}])
    out = _render_digest(_payload([card], companion_merged=1))
    assert "자회사 공시" in out
    assert "모회사 값과 섞이지 않게" in out
    assert "기준일 공시 1건을 이 카드에 묶었다" in out and "20261021800503" in out
    assert "기준일 공시를 배당 카드에 묶은 건 1건" in out


# ── details 기본값 ──────────────────────────────────────────────────────────────

@pytest.fixture
def small_universe(monkeypatch):
    """details 는 「열었다」만 기록하는 대역으로, 유니버스는 크기를 정해 끼운다."""
    opened: list[str] = []
    state = {"size": 3}

    async def _detail(h, running):
        opened.append(h["rcept_no"])
        return {"detail_status": "parsed", "fields": {"amount_won": 1}}

    async def _resolve(universe):
        if state["size"] is None:
            return S.UniverseFilter(label="전체시장", resolved=True)
        return S.UniverseFilter(label="지정", resolved=True,
                                allowed={f"{i:06d}" for i in range(state["size"])}, price_dd="20261021")

    async def _scan(client, code, bgn, end, pages):
        items = [_it(f"2026102180{i:04d}", "단일판매ㆍ공급계약 체결", corp=f"c{i}", stock=f"{i:06d}")
                 for i in range(3)] if code == "I001" else []
        return S._scan_result(items, len(items), 1, 1)

    monkeypatch.setattr(S, "_scan_code", _scan)
    monkeypatch.setattr(S, "_fetch_detail", _detail)
    monkeypatch.setattr(S, "resolve_universe", _resolve)
    monkeypatch.setattr(S, "get_dart_client", lambda: _FakeClient())
    monkeypatch.setattr(S, "_krx_mktcap_map", lambda codes, dd: {})
    return opened, state


def _call(**kw):
    kw.setdefault("start_date", "20261021")
    kw.setdefault("end_date", "20261021")
    return asyncio.run(S.build_screener_payload(types="order", universe="x", **kw))


def test_small_universe_and_short_period_open_details_by_default(small_universe):
    opened, _ = small_universe
    p = _call()
    assert len(opened) == 3, "숫자가 빠진 카드를 만들었다"
    assert p["types"]["details"] is True and p["types"]["details_auto"] is True
    assert any("details 를 기본으로 켰다" in w for w in p["warnings"]), "자동으로 켠 사실을 밝힌다"


def test_explicit_false_still_turns_it_off(small_universe):
    opened, _ = small_universe
    p = _call(details=False)
    assert opened == [] and p["types"]["details"] is False
    assert "details_auto" not in p["types"]


def test_explicit_true_is_not_reported_as_automatic(small_universe):
    opened, _ = small_universe
    p = _call(details=True)
    assert len(opened) == 3 and "details_auto" not in p["types"]


def test_big_or_unbounded_universe_stays_scan_only_by_default(small_universe):
    opened, state = small_universe
    state["size"] = 201
    assert _call()["types"]["details"] is False and opened == []
    state["size"] = None          # 전체시장
    assert _call()["types"]["details"] is False and opened == []


def test_long_period_stays_scan_only_by_default(small_universe):
    opened, _ = small_universe
    p = _call(start_date="20261001", end_date="20261021")
    assert p["types"]["details"] is False and opened == []


def test_boundary_values_are_inclusive(small_universe):
    """200종목·7일 폭은 켠다(이하)."""
    opened, state = small_universe
    state["size"] = 200
    p = _call(start_date="20261014", end_date="20261021")
    assert p["types"]["details"] is True and p["types"].get("details_auto") is True
