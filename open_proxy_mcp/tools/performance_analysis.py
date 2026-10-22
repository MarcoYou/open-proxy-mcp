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


def _attribution_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """업종 분해 md 표의 행 — 대상이 담은 업종(시작 비중 > 0)은 상위 N 자르기로 빼지 않고(B3) 모두 싣는다. 남는 자리는 효과가 큰 비보유 업종으로 채우고,
    그래도 잘린 비보유 업종은 「기타」 한 줄로 합쳐(비중·배분·선택·상호작용의 합) 표의 합이 총계와 맞게 한다. 입력은 효과 크기 순."""
    held = [g for g in rows if (g.get("w_p_pct") or 0.0) > 0.0]
    rest = [g for g in rows if (g.get("w_p_pct") or 0.0) <= 0.0]
    room = max(0, _TOP - len(held))
    keep = set(map(id, held)) | set(map(id, rest[:room]))
    shown = [g for g in rows if id(g) in keep]
    lumped = [g for g in rows if id(g) not in keep]
    if not lumped:
        return shown, None

    def tot(k: str) -> float:
        return sum((g.get(k) or 0.0) for g in lumped)
    return shown, {"n": len(lumped), "w_p_pct": tot("w_p_pct"), "w_b_pct": tot("w_b_pct"), "allocation_pct": tot("allocation_pct"),
                   "selection_pct": tot("selection_pct"), "interaction_pct": tot("interaction_pct")}


def _render(p: dict[str, Any]) -> str:
    d = p["data"]
    per, tg, m = d["period"], d["target"], d["metrics"]
    freq = "일별" if per["freq"] == "daily" else "주간"
    L = [f"# 성과 분석 — {tg['label']}", "",
         f"기간 {_d(per['start'])} ~ {_d(per['end'])} · {freq} {per['n_obs']}개 구간 · 달력 {per['calendar_days']:,}일"
         + (f" (요청 시작 {_d(per['requested_start'])})" if per["requested_start"] != per["start"] else "")]
    ix = tg.get("index")
    if ix:
        res = ix["resolution"]
        L += [f"대상 공식 지수 「{ix['name']}」(가격지수) · 시작 {_n(ix['start_point'])} → 끝 {_n(ix['end_point'])} 포인트 · 해상도 {freq} · 관측 {res['n_obs']:,}개 · "
              f"연환산 계수 {_n(res['ann_factor'], 1)}(표준 {res['nominal_ann_factor']})", ""]
    else:
        L += [f"대상 {tg['n_members']:,}종목 · 가중 {tg['weights_basis']} · 리밸런스 {tg['rebalance']} · 구성 기준일 {_d(tg['membership_asof'])}"
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
        rp = r.get("period")
        ptxt = f"{_d(rp['start'])} ~ {_d(rp['end'])}" if rp else "-"
        full = f"{_d(per['start'])} ~ {_d(per['end'])}"
        pts = (f"{_n(r.get('benchmark_start_point'))} → {_n(r.get('benchmark_end_point'))}" if r.get("benchmark_start_point") is not None else "-")
        mdd_b = (f"{_p(r.get('benchmark_mdd_pct'))} ({_d(r.get('benchmark_mdd_from'))} → {_d(r.get('benchmark_mdd_to'))})" if r.get("benchmark_mdd_from")
                 else _p(r.get("benchmark_mdd_pct")))
        # 이 표의 모든 값은 대상과 벤치마크를 맞대어 본 같은 겹친 기간 값이다 — 칸마다 그 기간을 적어, 위 표(전체 기간)와 섞여 읽히지 않게 한다
        L += [f"## 벤치마크 대비 — {r['benchmark']} ({'공식 지수' if r['benchmark_source'] == 'official' else '계산 벤치마크'})", "", "| 지표 | 값 | 기간 |", "|---|---|---|",
              f"| 대상 누적(같은 기간) | {_p(r.get('target_return_cum_pct'), 2, True)} | {ptxt} |",
              f"| 벤치마크 누적 | {_p(r['benchmark_return_cum_pct'], 2, True)} | {ptxt} |", f"| 초과수익(누적) | {_p(r['excess_cum_pct'], 2, True)} | {ptxt} |",
              f"| 초과수익(연율) | {_p(r['excess_ann_pct'], 2, True)} | {ptxt} |", f"| 베타 | {_n(r['beta'])} | {ptxt} |", f"| 알파(연율) | {_p(r['alpha_ann_pct'], 2, True)} | {ptxt} |",
              f"| 상관 | {_n(r['corr'])} | {ptxt} |", f"| 추적오차(연율) | {_p(r['tracking_error_pct'])} | {ptxt} |", f"| 정보비율 | {_n(r['info_ratio'])} | {ptxt} |",
              f"| 벤치마크 변동성(연율) | {_p(r.get('benchmark_vol_ann_pct'))} | {ptxt} |", f"| 벤치마크 최대 낙폭 | {mdd_b} | {ptxt} |",
              f"| 벤치마크 샤프 | {_n(r.get('benchmark_sharpe'))} | {ptxt} |", f"| 벤치마크 지수 포인트(시작 → 끝) | {pts} | {ptxt} |",
              f"| 맞대어 본 구간 | {r['n_obs_aligned']:,} / {per['n_obs']:,} | |", ""]
        if rp and (rp["start"] != per["start"] or rp["end"] != per["end"]):
            L += [f"> 이 표의 값은 대상과 벤치마크가 겹친 기간({ptxt}) 기준이다 — 위 「누적 수익률」은 전체 기간({full}) 값이라 기간이 다르다. 같은 기간의 대상 누적은 위 「대상 누적(같은 기간)」 행.", ""]
        if r.get("benchmark_points_reason"):
            L += [f"> 지수 포인트: {r['benchmark_points_reason']}", ""]
        if r.get("benchmark_reason"):
            L += [f"> {r['benchmark_reason']}", ""]
    elif "benchmark_same_as_target" in d.get("warning_codes", []):
        L += ["> 대상이 공식 지수 자체라 benchmark 를 비웠거나 같은 지수를 주면 자기 자신과 비교하게 되어 상대 지표(초과수익·베타·알파)는 내지 않았습니다(`benchmark_same_as_target`). "
              "다른 공식 지수나 계산 벤치마크를 benchmark 로 주세요.", ""]
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
        shown, other = _attribution_rows(a["by_group"])
        # 비중·효과는 소수 셋째 자리까지 — 행을 더해 합계와 맞춰 볼 수 있게(둘째 자리면 행마다 반올림 오차가 쌓여 합이 100% 에서 벗어난다)
        for g in shown:
            L.append(f"| {g['name']} | {_p(g['w_p_pct'], 3)} | {_p(g['w_b_pct'], 3)} | {_p(g['r_p_pct'], 2, True)} | {_p(g['r_b_pct'], 2, True)} | "
                     f"{_p(g['allocation_pct'], 3, True)} | {_p(g['selection_pct'], 3, True)} | {_p(g['interaction_pct'], 3, True)} |")
        if other:
            L.append(f"| 기타({other['n']:,}개 업종) | {_p(other['w_p_pct'], 3)} | {_p(other['w_b_pct'], 3)} | - | - | "
                     f"{_p(other['allocation_pct'], 3, True)} | {_p(other['selection_pct'], 3, True)} | {_p(other['interaction_pct'], 3, True)} |")
        L.append(f"| **합계** | {_p(sum((g.get('w_p_pct') or 0.0) for g in a['by_group']), 3)} | {_p(sum((g.get('w_b_pct') or 0.0) for g in a['by_group']), 3)} | - | - | "
                 f"{_p(a['allocation_pct'], 3, True)} | {_p(a['selection_pct'], 3, True)} | {_p(a['interaction_pct'], 3, True)} |")
        if other:
            L += ["", f"> 대상이 담은 업종은 모두 위에 있다. 효과가 작은 비보유 업종 {other['n']:,}개는 「기타」 한 줄로 합쳤다 — 「기타」와 위 행들의 합이 합계 줄과 맞는다. "
                      "업종별 전체는 format=\"json\" 의 data.attribution.by_group."]

        L += ["", f"> 비중은 {a['w_basis']}, 수익률은 {a['r_basis']}. 업종 분류 첫 관측 {_d(a['classification']['first_snapshot'])}"
                  + (" · 첫 관측 이전은 소급" if a["classification"]["retro"] else "") + ".", ""]
    if "series" in d:
        ms = _monthly(d["series"])
        tpt = "point" in ms[0]                                                    # 지수 target 이면 대상 포인트
        bpt = any(s.get("bench_point") is not None for s in ms)                    # 공식 지수 벤치마크면 벤치마크 포인트
        head = ["날짜", "대상"] + (["대상 포인트"] if tpt else []) + ["벤치마크"] + (["벤치마크 포인트"] if bpt else [])
        L += ["## 누적 수익률 추이 (월말 발췌)", "", "| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
        for s in ms[-36:]:
            cells = [_d(s["dd"]), _p(s["cum_pct"], 2, True)] + ([_n(s.get("point"))] if tpt else []) + [_p(s["bench_cum_pct"], 2, True)] + ([_n(s.get("bench_point"))] if bpt else [])
            L.append("| " + " | ".join(cells) + " |")
        L += ["", f"> 전 구간 {len(d['series']):,}점은 format=\"json\" 의 data.series. 위 표는 월말 발췌."]
        rp2 = (d.get("relative") or {}).get("period")
        if rp2 and rp2["start"] != per["start"]:
            L += ["", f"> 벤치마크 누적은 벤치마크가 시작하는 {_d(rp2['start'])} 을 0% 로 센다 — 대상 누적({_d(per['start'])} 기준)과 시작일이 달라 두 열을 직접 빼지 않는다."]
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
        """desc: 성과 분석 — 종목·업종·순위·바스켓·블렌드(여러 구성요소를 비중으로 섞은 것)와 **공식 지수 자체**(코스피·코스닥 등)의 수익률을 **부를 때 계산**한다. 누적·연율 수익률, 변동성·하방 변동성·최대 낙폭, 샤프·소르티노, 벤치마크 대비 초과수익·베타·알파·추적오차·정보비율, 종목·업종·구성요소별 **기여 분해**(Carino 연결), 업종 **Brinson-Fachler 분해**(배분·선택·상호작용), 누적 시계열. 가격 수익률 또는 세전 총수익(배당 재투자). 저장된 시세·수정 이벤트·지수·금리·배당 원장만 읽는다(DART·KRX·ECOS 0콜, 쓰기 0).
        when: "코스피 1년 수익률·변동성·최대낙폭이 어때"(target=코스피)·"반도체 업종 최근 1년 수익률이 코스피 대비 얼마야"·"삼성전자 60 SK하이닉스 40 포트폴리오 변동성·낙폭"·"업종 수익률을 종목 기여로 쪼개 줘"(scope=contribution)·"코스피 시총 상위 50 이 코스피보다 업종 배분 덕인지 종목 선택 덕인지"(scope=attribution)·"반도체 60% 금융 40% 섞으면 샤프가 어때"(블렌드)·"배당 포함 수익률"(basis=total). 시세 시계열 그 자체는 `trading_data`, 배수는 `price_multiple_data`, 배당 이력은 `dividend_data`.
        rule: target = **공식 지수 자체**(단일 문구 「코스피」「코스닥」 또는 「지수:」 뒤 지수 이름 — 예 "지수:코스피 200" — DB 에 일별 종가가 있는 계열만, 없으면 「이 지수는 DB 에 일별 종가가 없다」 invalid)이거나 `trading_data(scope="universe")` 와 같은 문구 — 업종("반도체 업종"·"중분류 반도체와반도체장비"·"대분류 IT"·"코스피 IT 섹터 상위 10") · 순위·지수("코스피 시총 상위 50"·"코스피200") · 나열("005930, 000660") · 바스켓("005930:40, 000660:60" — 종목:비중%) · 블렌드(구성요소를 `;` 로 나누고 `=비중%`: "반도체 업종=60; 대분류 금융=40"). 블렌드·바스켓 비중 합이 100(±0.5)이 아니면 **맞춰 채우지 않고 되묻는다**(invalid). 구성요소 최대 10개, 구성 종목 일별 500·주간 300, 시세 행 20만 이하. 「코스피200」「코스피 200」「코스닥150」「코스닥 150」「코스피 전체」와 블렌드 안의 지수 이름은 종전대로 **구성 종목 바스켓**이다(지수 자체를 고르려면 「지수:」 접두사). 지수 target 은 plan.freq 를 따라 일별이면 지수 일별 행만, 주간이면 주 닫는 날 점만 쓰고(한 계산에 섞지 않음, 응답 `target.index.resolution` 에 해상도·관측 수·연환산 계수 — 주간은 주 종가 기준이라 그 사이 낙폭을 놓칠 수 있다 `index_weekly_resolution`), 시작·끝 지수 포인트를 `target.index` 에 싣는다. 지수 target 에 benchmark 를 비우거나 같은 지수를 주면 자기 비교라 `relative` 를 내지 않고 `benchmark_same_as_target` 경고를 달며, scope=attribution·contribution 은 구성 이력이 없어 invalid 다. **구성은 시작일 시점으로 고정**(업종 문구는 시작일의 분류 스냅샷, 시총 순위·지수 문구는 보관된 최신 거래일 구성이라 `membership_lookahead` 경고), 기간 중 상장폐지는 마지막 값 뒤 0% 현금, 시작일 시세가 없는 종목은 빼고 알린다. 종가를 그대로 나누지 않는다 — 일별은 KRX 공표 전일대비(cmp, 분할·권리락 반영)로, 주간은 종가 ÷ (전 점 종가 × Π 기준가 사건 원값 base/prev) 로 계산하고 호가단위 맞춤 사건은 뺀다. 일별 창(약 1년) 안이면 일별, 아니면 주간(주 마지막 거래일 한 점, 3년 밖은 보관본에서 잇고 못 읽으면 DB 하한으로 잘라 `cold_coverage` 경고 — 부분 곡선 없음). 누적 = Π(1+r)−1, 연율 = (1+R)^(365.25/일수)−1(365일 미만이면 비움), 변동성 = 표본 표준편차×√A, MDD = 부(wealth) 곡선, 무위험 금리 = 연율 % × 구간 달력일/365(ACT/365 단리, 구간 시작 이전 값), 샤프 = mean(r−rf)/sd(r−rf)×√A, 베타·알파 = 초과수익끼리 OLS. 총수익(basis=total)은 배당락일 구간에 DPS/조정 기준가를 더한 **세전** 값(`gross_total_return` — 세후 NTR 과 비교하지 않는다), 배당락일 = 기준일 이하 마지막 거래일의 앞 거래일(12-31 휴장이면 12-29), 일별 창 밖은 평일 근사(`ex_date_basis`), 결의일이 끝 기준일 뒤인 배당은 넣지 않고(`dividend_pending`), 우선주·원장 밖 몫은 가격 수익률(`dividend_coverage`). 벤치마크: 비우면 시작일 비중이 큰 시장의 공식 지수(코스피·코스닥, 외국주 제외 가격지수); 공식 지수 이름("코스피200"·"KRX 300"·"코스닥 전기전자" — 코스피·코스닥에 같은 이름이면 되묻는다) 또는 유니버스 문구("코스피 전체"·"코스닥 전체"=우선주를 뺀 보통주 시총 가중 계산 벤치마크(공식 코스피·코스닥 지수와 모집단이 다를 수 있다 — 지수 이름으로 고르면 공식 지수), "대분류 IT" 등=시작일 구성 고정 — 업종 문구의 구성 종목에도 우선주는 없다. 종목 코드·바스켓으로 직접 적은 우선주는 그대로). 공식 지수·금리는 최근 3년뿐이라 그 밖은 겹치는 구간만(`benchmark_truncated`·`rf_unavailable`) — 이때 `relative` 의 대상 누적(`target_return_cum_pct`)·벤치마크 누적·초과수익은 모두 같은 겹친 기간(`relative.period`)이고 대상 전체 기간의 `metrics` 와 기간이 다르다. `relative` 에는 벤치마크 변동성·최대낙폭(날짜)·샤프와 시작·끝 지수 포인트(공식 지수만, 계산 벤치마크는 null 과 `benchmark_points_reason`)가 같이 나가고 scope=series 는 `bench_point`(지수 종가)를 함께 준다. scope=attribution md 업종 표는 대상이 담은 업종을 자르지 않고 잘린 비보유 업종은 「기타」 한 줄로 합쳐 합계가 맞는다. scope=attribution 은 구성 이력이 있는 계산 벤치마크만 된다 — 공식 지수를 주면 invalid 와 안내. 업종은 시점 기준(그 기간 시작일의 분류 구간), 2019-06-28 이전은 WICS 체계 개편 앞이라 `sector_taxonomy_break`, 첫 분류 스냅샷 이전은 소급(`sector_retroactive`)·생존 편향(`survivorship`). 응답의 `warning_codes` 가 기계가 읽는 경고다.
        status: ok / invalid(되물음 — 블렌드 비중 합·모호한 지수 이름·attribution+공식 지수·한도 초과) / not_found(해당 종목 없음) / no_data / db_error(일시 장애 — 재시도 유효) / **db_unconfigured**(이 서버에 스냅샷 DB 미연결 — 재시도해도 안 됨). 무위험 금리·배당·딱지·콜드·벤치마크 읽기 실패는 db_error 로 올리지 않고 그 지표만 비운 뒤 경고한다.
        params: target(필수) 위 rule 문법(공식 지수 자체 포함). weights = 구성요소 안 가중 "시총"(시작일 시총, 기본)/"동일" — 바스켓은 적은 비중이 이긴다. since = 시작일(비우면 끝 기준일의 1년 전, 휴장이면 앞 거래일). as_of = 끝 기준일(비우면 보관된 최신 거래일). benchmark = 위 rule. scope = summary(수익·위험·상대, 기본)/contribution(기여 분해)/attribution(업종 Brinson)/series(누적 시계열). group_by = 중분류(기본)/대분류/종목/구성요소 — attribution 은 중분류·대분류만. basis = price(기본)/total(세전 총수익). rebalance = none(표류, 기본)/monthly/quarterly(새 달·분기 첫 거래일에 목표 비중으로). rf = 비우면 국고채 1년, 숫자(연 %)면 고정 금리. freq = 비우면 자동(기간이 일별 창 안이면 daily, 아니면 weekly), daily 를 창 밖에서 요구하면 invalid. format = md/json.
        ref: trading_data, price_multiple_data, dividend_data
        """
        payload = await build_performance_payload(target, weights=weights, since=since, as_of=as_of, benchmark=benchmark, scope=scope,
                                                  group_by=group_by, basis=basis, rebalance=rebalance, rf=rf, freq=freq)
        if format == "json":
            return as_pretty_json(payload)
        if payload.get("status") != "ok":
            return _render_status(payload)
        return _render(payload)
