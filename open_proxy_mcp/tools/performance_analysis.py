"""performance_analysis public tool — 블렌딩·초과수익·기여 분해·위험 지표를 부를 때 계산한다 (261022).

겉면만 둔다: 인자 → `services/performance_analysis.build_performance_payload` → md 또는 json. 계산식은 `services/perf_math.py`, 읽기는 `services/perf_data.py`.
"""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.services.performance_analysis import build_performance_payload

_STATUS_TITLE = {
    "invalid": "입력 오류 — 되묻는 말 또는 한도 초과",
    "not_found": "조회 결과 없음",
    "no_data": "데이터 없음",
    "db_error": "DB 연결 실패 (일시 장애 — 잠시 후 재시도)",
    # ★ 위와 갈라 둔다. 「일시 장애」는 재시도가 답이지만 미연결은 재시도가 답이 아니다.
    "db_unconfigured": "스냅샷 DB 미연결 — 이 서버에서는 제공되지 않음",
}


def _p(x: float | None, nd: int = 2, sign: bool = False) -> str:
    if x is None:
        return "-"
    return f"{x:+,.{nd}f}%" if sign else f"{x:,.{nd}f}%"


def _n(x: float | None, nd: int = 2) -> str:
    return "-" if x is None else f"{x:,.{nd}f}"


def _d(dd: str | None) -> str:
    return f"{dd[:4]}-{dd[4:6]}-{dd[6:]}" if dd and len(dd) == 8 else (dd or "-")


def _render_status(p: dict[str, Any]) -> str:
    lines = [f"# performance_analysis: {p.get('subject', '')} — {_STATUS_TITLE.get(p.get('status'), p.get('status'))}", ""]
    lines += [f"- {w}" for w in p.get("warnings", [])]
    return "\n".join(lines)


def _monthly(series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """월말 다운샘플 — 일별 250점·주간 150점을 표로 쏟지 않는다(json 에는 전량 남는다). 같은 달이면 뒤가 이긴다."""
    out: dict[str, dict[str, Any]] = {}
    for s in series:
        out[s["dd"][:6]] = s
    return [out[k] for k in sorted(out)]


_TOP = 20


def _render(p: dict[str, Any]) -> str:
    d = p["data"]
    per, tg, m = d["period"], d["target"], d["metrics"]
    freq = "일별" if per["freq"] == "daily" else "주간"
    L = [f"# 성과 분석 — {tg['label']}", "",
         f"기간 {_d(per['start'])} ~ {_d(per['end'])} · {freq} {per['n_obs']}개 구간 · 달력 {per['calendar_days']:,}일"
         + (f" (요청 시작 {_d(per['requested_start'])})" if per["requested_start"] != per["start"] else ""),
         f"대상 {tg['n_members']:,}종목 · 가중 {tg['weights_basis']} · 리밸런스 {tg['rebalance']} · 구성 기준일 {_d(tg['membership_asof'])}"
         + (" · 총수익(세전)" if d["coverage"]["dividend"]["basis"] == "total" else " · 가격 수익률"), ""]
    if len(tg["components"]) > 1:
        L += ["## 구성요소", "", "| 구성요소 | 비중 | 종목 수 | 구성 기준일 |", "|---|---|---|---|"]
        for c in tg["components"]:
            L.append(f"| {c['label']} | {_p(c['weight_pct'], 1)} | {c['n_members']:,} | {_d(c['membership_asof'])} |")
        L.append("")
    L += ["## 수익·위험", "", "| 지표 | 값 |", "|---|---|",
          f"| 누적 수익률 | {_p(m['return_cum_pct'], 2, True)} |",
          f"| 연율 수익률 | {_p(m['return_ann_pct'], 2, True)} |",
          f"| 변동성(연율) | {_p(m['vol_ann_pct'])} |",
          f"| 하방 변동성(연율) | {_p(m['downside_vol_ann_pct'])} |",
          f"| 최대 낙폭 | {_p(m['mdd_pct'])} ({_d(m['mdd_from'])} → {_d(m['mdd_to'])}) |",
          f"| 샤프 | {_n(m['sharpe'])} |", f"| 소르티노 | {_n(m['sortino'])} |", ""]
    if m["return_ann_pct"] is None:
        L += ["> 기간이 365일 미만이라 연율 수익률은 내지 않았습니다.", ""]
    r = d.get("relative")
    if r:
        L += [f"## 벤치마크 대비 — {r['benchmark']} ({'공식 지수' if r['benchmark_source'] == 'official' else '계산 벤치마크'})", "", "| 지표 | 값 |", "|---|---|",
              f"| 벤치마크 누적 | {_p(r['benchmark_return_cum_pct'], 2, True)} |", f"| 초과수익(누적) | {_p(r['excess_cum_pct'], 2, True)} |",
              f"| 초과수익(연율) | {_p(r['excess_ann_pct'], 2, True)} |", f"| 베타 | {_n(r['beta'])} |", f"| 알파(연율) | {_p(r['alpha_ann_pct'], 2, True)} |",
              f"| 상관 | {_n(r['corr'])} |", f"| 추적오차(연율) | {_p(r['tracking_error_pct'])} |", f"| 정보비율 | {_n(r['info_ratio'])} |",
              f"| 맞대어 본 구간 | {r['n_obs_aligned']:,} / {per['n_obs']:,} |", ""]
        if r.get("benchmark_reason"):
            L += [f"> {r['benchmark_reason']}", ""]
    else:
        L += ["> 벤치마크를 읽지 못해 상대 지표는 비웠습니다.", ""]
    rf = d["rf"]
    L += [f"> 무위험 금리: {rf['series']} · 평균 {_p(rf['avg_pct'], 3)} · 기준 {_d(rf['asof'])} · {rf['convention']}"
          + (f" · 금리 없는 구간 {rf['missing']}개" if rf.get("missing") else ""), ""]
    if "contributions" in d:
        rows = d["contributions"]
        by = d.get("contributions_by", "종목")
        L += [f"## 기여 분해 — {by}별 (상위 {min(_TOP, len(rows))}개, Carino 연결 — 합 = 누적 수익률)", "",
              "| " + ("종목 | 코드" if by == "종목" else "묶음 | 코드") + " | 구성요소 | 업종 | 시작 비중 | 끝 비중 | 수익률 | 기여 |", "|---|---|---|---|---|---|---|---|"]
        for c in rows[:_TOP]:
            L.append(f"| {c['name']} | `{c['ticker']}` | {c.get('component') or '-'} | {c.get('sector') or '-'} | {_p(c['weight_start_pct'])} | "
                     f"{_p(c['weight_end_pct'])} | {_p(c['return_pct'], 2, True)} | {_p(c['contribution_pct'], 2, True)} |")
        if len(rows) > _TOP:
            L += ["", f"> 표는 기여 상위 {_TOP}개까지. 전체 {len(rows):,}개는 format=\"json\" 의 data.contributions."]
        L.append("")
    if "attribution" in d:
        a = d["attribution"]
        L += [f"## 업종 분해 — Brinson-Fachler ({a['group_by']}, 초과 Carino 연결)", "",
              f"배분 {_p(a['allocation_pct'], 2, True)} + 선택 {_p(a['selection_pct'], 2, True)} + 상호작용 {_p(a['interaction_pct'], 2, True)} "
              f"= 초과수익 {_p(a['active_pct'], 2, True)} (오차 {a['residual_pct']:+.6f}%p)", "",
              "| 업종 | 대상 비중 | 벤치마크 비중 | 대상 수익률 | 벤치마크 수익률 | 배분 | 선택 | 상호작용 |", "|---|---|---|---|---|---|---|---|"]
        for g in a["by_group"][:_TOP]:
            L.append(f"| {g['name']} | {_p(g['w_p_pct'])} | {_p(g['w_b_pct'])} | {_p(g['r_p_pct'], 2, True)} | {_p(g['r_b_pct'], 2, True)} | "
                     f"{_p(g['allocation_pct'], 2, True)} | {_p(g['selection_pct'], 2, True)} | {_p(g['interaction_pct'], 2, True)} |")
        if len(a["by_group"]) > _TOP:
            L += ["", f"> 표는 효과가 큰 상위 {_TOP}개까지. 전체 {len(a['by_group']):,}개는 format=\"json\" 의 data.attribution.by_group."]
        L += ["", f"> 비중은 {a['w_basis']}, 수익률은 {a['r_basis']}. 업종 분류 첫 관측 {_d(a['classification']['first_snapshot'])}"
                  + (" · 첫 관측 이전은 소급" if a["classification"]["retro"] else "") + ".", ""]
    if "series" in d:
        ms = _monthly(d["series"])
        L += ["## 누적 수익률 추이 (월말 발췌)", "", "| 날짜 | 대상 | 벤치마크 |", "|---|---|---|"]
        for s in ms[-36:]:
            L.append(f"| {_d(s['dd'])} | {_p(s['cum_pct'], 2, True)} | {_p(s['bench_cum_pct'], 2, True)} |")
        L += ["", f"> 전 구간 {len(d['series']):,}점은 format=\"json\" 의 data.series. 위 표는 월말 발췌."]
    cov = d["coverage"]
    L += ["", "## 커버리지", "",
          f"- 사건 보정 {cov['adj_events_used']:,}건 사용 · 호가단위 맞춤 {cov['tick_rounding_skipped']:,}건 제외 · 공표 대비 없는 행 {cov['cmp_missing_rows']:,}개"]
    if cov["delisted_in_period"]:
        L.append(f"- 기간 중 상장폐지 등 {len(cov['delisted_in_period'])}종목은 마지막 값 뒤 현금: "
                 + ", ".join(f"`{x['ticker']}`({_d(x['last_dd'])})" for x in cov["delisted_in_period"][:10])
                 + (" …" if len(cov["delisted_in_period"]) > 10 else ""))
    dv = cov["dividend"]
    if dv["basis"] == "total":
        L.append(f"- 배당(세전): 반영 비중 {_p(dv.get('covered_weight_pct'), 1)} · 우선주 {dv.get('preferred_price_only', 0)}종목은 가격 수익률 · "
                 f"결의 전 {dv.get('n_pending', 0)}건 제외 · 배당락일 {dv.get('ex_date_basis') or '-'}")
    if cov["cold"]["used"]:
        L.append(f"- 3년 밖은 보관본에서 이음 (이음매 {_d(cov['cold']['seam'])})")
    L += ["", f"> {d['method']}"] + [f"> ⚠ {w}" for w in p.get("warnings", [])]
    return "\n".join(L)


def register_tools(mcp):

    @mcp.tool()
    async def performance_analysis(target: str, weights: str = "시총", since: str = "", as_of: str = "", benchmark: str = "",
                                   scope: str = "summary", group_by: str = "중분류", basis: str = "price", rebalance: str = "none",
                                   rf: str = "", freq: str = "", format: str = "md") -> str:
        """desc: 성과 분석 — 종목·업종·순위·바스켓·블렌드(여러 구성요소를 비중으로 섞은 것)의 수익률을 **부를 때 계산**한다. 누적·연율 수익률, 변동성·하방 변동성·최대 낙폭, 샤프·소르티노, 벤치마크 대비 초과수익·베타·알파·추적오차·정보비율, 종목·업종·구성요소별 **기여 분해**(Carino 연결), 업종 **Brinson-Fachler 분해**(배분·선택·상호작용), 누적 시계열. 가격 수익률 또는 세전 총수익(배당 재투자). 저장된 시세·수정 이벤트·지수·금리·배당 원장만 읽는다(DART·KRX·ECOS 0콜, 쓰기 0).
        when: "반도체 업종 최근 1년 수익률이 코스피 대비 얼마야"·"삼성전자 60 SK하이닉스 40 포트폴리오 변동성·낙폭"·"업종 수익률을 종목 기여로 쪼개 줘"(scope=contribution)·"코스피 시총 상위 50 이 코스피보다 업종 배분 덕인지 종목 선택 덕인지"(scope=attribution)·"반도체 60% 금융 40% 섞으면 샤프가 어때"(블렌드)·"배당 포함 수익률"(basis=total). 시세 시계열 그 자체는 `trading_data`, 배수는 `price_multiple_data`, 배당 이력은 `dividend_data`.
        rule: target = `trading_data(scope="universe")` 와 같은 문구 — 업종("반도체 업종"·"중분류 반도체와반도체장비"·"대분류 IT"·"코스피 IT 섹터 상위 10") · 순위·지수("코스피 시총 상위 50"·"코스피200") · 나열("005930, 000660") · 바스켓("005930:40, 000660:60" — 종목:비중%) · 블렌드(구성요소를 `;` 로 나누고 `=비중%`: "반도체 업종=60; 대분류 금융=40"). 블렌드·바스켓 비중 합이 100(±0.5)이 아니면 **맞춰 채우지 않고 되묻는다**(invalid). 구성요소 최대 10개, 구성 종목 일별 500·주간 300, 시세 행 20만 이하. **구성은 시작일 시점으로 고정**(업종 문구는 시작일의 분류 스냅샷, 시총 순위·지수 문구는 보관된 최신 거래일 구성이라 `membership_lookahead` 경고), 기간 중 상장폐지는 마지막 값 뒤 0% 현금, 시작일 시세가 없는 종목은 빼고 알린다. 종가를 그대로 나누지 않는다 — 일별은 KRX 공표 전일대비(cmp, 분할·권리락 반영)로, 주간은 종가 ÷ (전 점 종가 × Π 기준가 사건 원값 base/prev) 로 계산하고 호가단위 맞춤 사건은 뺀다. 일별 창(약 1년) 안이면 일별, 아니면 주간(주 마지막 거래일 한 점, 3년 밖은 보관본에서 잇고 못 읽으면 DB 하한으로 잘라 `cold_coverage` 경고 — 부분 곡선 없음). 누적 = Π(1+r)−1, 연율 = (1+R)^(365.25/일수)−1(365일 미만이면 비움), 변동성 = 표본 표준편차×√A, MDD = 부(wealth) 곡선, 무위험 금리 = 연율 % × 구간 달력일/365(ACT/365 단리, 구간 시작 이전 값), 샤프 = mean(r−rf)/sd(r−rf)×√A, 베타·알파 = 초과수익끼리 OLS. 총수익(basis=total)은 배당락일 구간에 DPS/조정 기준가를 더한 **세전** 값(`gross_total_return` — 세후 NTR 과 비교하지 않는다), 배당락일 = 기준일 이하 마지막 거래일의 앞 거래일(12-31 휴장이면 12-29), 일별 창 밖은 평일 근사(`ex_date_basis`), 결의일이 끝 기준일 뒤인 배당은 넣지 않고(`dividend_pending`), 우선주·원장 밖 몫은 가격 수익률(`dividend_coverage`). 벤치마크: 비우면 시작일 비중이 큰 시장의 공식 지수(코스피·코스닥, 외국주 제외 가격지수); 공식 지수 이름("코스피200"·"KRX 300"·"코스닥 전기전자" — 코스피·코스닥에 같은 이름이면 되묻는다) 또는 유니버스 문구("코스피 전체"·"코스닥 전체"=우선주 포함 시총 가중 계산 벤치마크, "대분류 IT" 등=시작일 구성 고정). 공식 지수·금리는 최근 3년뿐이라 그 밖은 겹치는 구간만(`benchmark_truncated`·`rf_unavailable`). scope=attribution 은 구성 이력이 있는 계산 벤치마크만 된다 — 공식 지수를 주면 invalid 와 안내. 업종은 시점 기준(그 기간 시작일의 분류 구간), 2019-06-28 이전은 WICS 체계 개편 앞이라 `sector_taxonomy_break`, 첫 분류 스냅샷 이전은 소급(`sector_retroactive`)·생존 편향(`survivorship`). 응답의 `warning_codes` 가 기계가 읽는 경고다.
        status: ok / invalid(되물음 — 블렌드 비중 합·모호한 지수 이름·attribution+공식 지수·한도 초과) / not_found(해당 종목 없음) / no_data / db_error(일시 장애 — 재시도 유효) / **db_unconfigured**(이 서버에 스냅샷 DB 미연결 — 재시도해도 안 됨). 무위험 금리·배당·딱지·콜드·벤치마크 읽기 실패는 db_error 로 올리지 않고 그 지표만 비운 뒤 경고한다.
        params: target(필수) 위 rule 문법. weights = 구성요소 안 가중 "시총"(시작일 시총, 기본)/"동일" — 바스켓은 적은 비중이 이긴다. since = 시작일(비우면 끝 기준일의 1년 전, 휴장이면 앞 거래일). as_of = 끝 기준일(비우면 보관된 최신 거래일). benchmark = 위 rule. scope = summary(수익·위험·상대, 기본)/contribution(기여 분해)/attribution(업종 Brinson)/series(누적 시계열). group_by = 중분류(기본)/대분류/종목/구성요소 — attribution 은 중분류·대분류만. basis = price(기본)/total(세전 총수익). rebalance = none(표류, 기본)/monthly/quarterly(새 달·분기 첫 거래일에 목표 비중으로). rf = 비우면 국고채 1년, 숫자(연 %)면 고정 금리. freq = 비우면 자동(기간이 일별 창 안이면 daily, 아니면 weekly), daily 를 창 밖에서 요구하면 invalid. format = md/json.
        ref: trading_data, price_multiple_data, dividend_data
        """
        payload = await build_performance_payload(target, weights=weights, since=since, as_of=as_of, benchmark=benchmark, scope=scope,
                                                  group_by=group_by, basis=basis, rebalance=rebalance, rf=rf, freq=freq)
        if format == "json":
            return as_pretty_json(payload)
        if payload.get("status") != "ok":
            return _render_status(payload)
        return _render(payload)
