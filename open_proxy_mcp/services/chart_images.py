"""Report figures made from one completed tool response; no queries or result storage."""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from functools import lru_cache, partial
from io import StringIO
from math import isfinite, nan
from textwrap import fill
from threading import Lock

import matplotlib as mpl
from matplotlib import dates, font_manager
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.backends.backend_svg import FigureCanvasSVG
from matplotlib.figure import Figure

from .chart_app_marks import ChartMarks

_LOCK = Lock()  # rc_context and font handling are shared across Agg figures.
# Financial series encode identity. Only the governance table assigns colors to states.
_INK = "#17272F"
_MUTED = "#52656A"
_RULE = "#D9E0DD"
_SURFACE = "#FFFFFF"
_PRIMARY = "#2C7E97"
_REFERENCE = "#7B8492"
_CLASS_HATCHES = ("//", "..", "xx", "++", "oo", "**")
_STATE_COLORS = {
    "준수": ("#2D8465", "#E6F0EC"),
    "미준수": ("#B03432", "#F7EBEA"),
    "해당 없음": (_MUTED, "#E9EEEA"),
    "미상": (_MUTED, _SURFACE),
}


@lru_cache(maxsize=1)
def _font():
    for family in ("NanumGothic", "Apple SD Gothic Neo", "Malgun Gothic", "Noto Sans CJK KR"):
        try:
            font_manager.findfont(family, fallback_to_default=False)
        except ValueError:
            continue
        return family
    raise RuntimeError("A Korean font is required to render financial charts")


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def _figure(title, height=4.5, width=7.0):
    fig = Figure(figsize=(width, height), dpi=125, layout="constrained")
    fig.chart_marks = ChartMarks()
    fig.suptitle(fill(title, width=55), x=.025, ha="left", fontsize=16, fontweight="bold", color=_INK)
    return fig


def _table(head, rows):
    def line(values):
        return "| " + " | ".join(str(v).replace("|", "／").replace("\n", " ") for v in values) + " |"
    return "\n".join([line(head), line(["---"] * len(head)), *(line(r) for r in rows)])


def _performance(p):
    d = p["data"]
    rows = d.get("series") or []
    if not any(_number(r.get("cum_pct")) for r in rows):
        return
    tg = d["target"]["label"]
    rel = d.get("relative") or {}
    bench = rel.get("benchmark") or "비교지수"
    per = d["period"]
    basis = "세전 총수익" if d["coverage"]["dividend"]["basis"] == "total" else "가격 수익률"
    title = f"{tg} · 누적 {basis}"
    aligned = rel.get("period") or {}
    split = bool(aligned.get("start") and aligned["start"] != per["start"])
    fig = _figure(title, 6 if split else 4.5)
    x = [datetime.strptime(r["dd"], "%Y%m%d") for r in rows]
    axes = fig.subplots(2 if split else 1, sharex=True, squeeze=False).ravel()
    def percent(v):
        return f"{v:,.2f}%" if _number(v) else "자료 없음"
    groups = [fig.chart_marks.group(
        r["dd"], f"{r['dd']} · {tg} {percent(r.get('cum_pct'))} · {bench} {percent(r.get('bench_cum_pct'))}"
    ) for r in rows]
    for i, (name, field) in enumerate(((tg, "cum_pct"), (bench, "bench_cum_pct"))):
        vals = [r[field] if _number(r.get(field)) else nan for r in rows]
        if not any(isfinite(v) for v in vals):
            continue
        ax = axes[-1] if i else axes[0]
        ax.plot(x, vals, color=(_PRIMARY, _REFERENCE)[i], linestyle="--" if i else "-", label=fill(name, 45),
                marker="." if len(rows) < 40 else None, linewidth=2)
        for group, day, val in zip(groups, x, vals):
            if isfinite(val):
                fig.chart_marks.point(group, ax, day, val)
    for ax in axes:
        ax.set_ylabel("누적수익률 (%)")
        ax.axhline(0, color=_REFERENCE, linewidth=.9, zorder=1)
        ax.legend(frameon=False, loc="lower left", bbox_to_anchor=(0, 1), borderaxespad=0, fontsize=9)
        locator = dates.AutoDateLocator(minticks=2, maxticks=6)
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(dates.DateFormatter("%m/%d" if (x[-1]-x[0]).days < 180 else "%Y-%m"))
    note = f"{per['start']} – {per['end']} · {basis} · OPM 성과 분석"
    if split:
        note += f" · 비교계열 기준일 {aligned['start']}"
    axes[-1].set_xlabel(note)
    sample = rows if len(rows) <= 6 else [rows[0], *rows[-5:]]
    table = _table(["거래일", tg, bench], [(r["dd"], percent(r.get("cum_pct")), percent(r.get("bench_cum_pct"))) for r in sample])
    return fig, f"**{title}** · {note}\n\n{table}\n\n표는 최대 여섯 거래일을 발췌했습니다.\n자료: " + " · ".join(d.get("sources", []))


def _dividend(p):
    d = p["data"]
    annual = d.get("annual") or []
    if not annual or d.get("annual_failed"):
        return
    # Distinct stock classes and fiscal closings are independent observations.
    obs = {}
    common = set()
    for r in annual:
        period = str(r.get("stlm_dt") or r["bsns_year"])
        kind = (r.get("stock_knd_raw") or r.get("stock_kind") or "미구분").strip()
        if r.get("stock_kind") == "보통" or kind in ("보통주", "보통"):
            common.add(kind)
        key = period, kind
        if key in obs:
            return  # No unambiguous bar for this closing and class.
        obs[key] = r
    kinds = sorted({k for _, k in obs}, key=lambda k: (k not in common, k))
    periods = sorted({t for t, _ in obs})
    if len(kinds) > len(_CLASS_HATCHES) or len(periods) > 30:
        return
    title = f"{p['subject']} · 결산기별 주당배당금"
    fig = _figure(title)
    ax = fig.subplots()
    width = .8 / len(kinds)
    shown = []
    table_rows = []
    secondary = [kind for kind in kinds if kind not in common]
    for j, kind in enumerate(kinds):
        x, y, groups = [], [], []
        for i, period in enumerate(periods):
            r = obs.get((period, kind))
            if not r:
                continue
            val = r.get("dps_krw")
            confirmed = r.get("row_status") == "확정" and _number(val)
            table_rows.append((period, kind, f"{val:,}" if confirmed else "—", r.get("row_status") or "미상", r.get("rcept_no") or "—"))
            if confirmed:
                x.append(i + width * (j - (len(kinds) - 1) / 2))
                y.append(val)
                groups.append(fig.chart_marks.group(f"{period} · {kind}", f"{period} · {kind} · 주당 {val:,}원"))
        if y:
            color = _PRIMARY if kind in common else _REFERENCE
            hatch = None if kind in common else _CLASS_HATCHES[secondary.index(kind)]
            bars = ax.bar(x, y, width=width*.92, color=color, edgecolor=_SURFACE,
                          linewidth=.5, hatch=hatch, label=kind)
            for group, bar in zip(groups, bars):
                fig.chart_marks.artist(group, bar)
            shown.extend(y)
            if len(obs) <= 16:
                ax.bar_label(bars, labels=[f"{v:,}".removesuffix(".0") for v in y], fontsize=8, padding=2)
    if not shown:
        return
    ax.set_ylim(0, max(1, max(shown) * 1.18))
    ax.set_xticks(range(len(periods)), periods, rotation=45 if len(periods)>5 else 0, fontsize=8)
    ax.set_ylabel("주당배당금 (원)")
    ax.legend(frameon=False, loc="lower left", bbox_to_anchor=(0, 1), borderaxespad=0, ncols=min(3, len(kinds)), fontsize=9)
    ax.set_xlabel("결산기 말 · DART 정기보고서 · 확정값 기준")
    return fig, f"**{title}** · 확정값, 원/주 · DART 정기보고서\n\n" + _table(["결산기", "주식 종류", "원/주", "상태", "접수번호"], sorted(table_rows))


def _governance(p):
    d = p["data"]
    rows = d.get("metrics") or []
    if not rows:
        return
    labels = {"O": "준수", "○": "준수", "X": "미준수", "×": "미준수", "해당없음": "해당 없음"}
    def status(v):
        return labels.get(str(v or "").replace(" ", ""), "미상")
    data = [(r["label"], status(r.get("prior")), status(r.get("current"))) for r in rows]
    meta = d.get("report_meta") or {}
    title = f"{d.get('canonical_name') or p.get('subject', '')} · 지배구조 핵심지표"
    caption = f"기준 {meta.get('reporting_period_end') or '미상'} · 공시 {meta.get('rcept_dt') or '미상'} · DART {meta.get('rcept_no') or ''}"
    wrapped = [fill(label, width=26) for label, _, _ in data]
    heights = [max(1.4, (s.count("\n")+1)*.92) for s in wrapped]
    fig = _figure(title, max(3, 1.4 + sum(heights)*.28), width=5.6)
    ax = fig.subplots()
    ax.axis("off")
    bottom = sum(heights)
    ax.set(xlim=(0,1), ylim=(-1.4,bottom+1.8))
    ax.text(.01, bottom+.85, "핵심지표", weight="bold", color=_MUTED)
    for x, heading in ((.76,"직전기"),(.92,"당기")):
        ax.text(x,bottom+.85,heading,ha="center",weight="bold")
    y = bottom
    for label, height, (_, prior, curr) in zip(wrapped, heights, data):
        y -= height/2
        name = label.replace("\n", " ")
        group = fig.chart_marks.group(name, f"{name} · 직전기 {prior} · 당기 {curr}")
        fig.chart_marks.artist(group, ax.text(.01,y,label,va="center",fontsize=9.5))
        for x,s in ((.76,prior),(.92,curr)):
            border, face = _STATE_COLORS[s]
            badge = ax.text(x, y, s, ha="center", va="center", fontsize=10, color=_INK,
                    weight="bold" if x == .92 else "normal",
                    bbox=dict(facecolor=face, edgecolor=border, linewidth=.8,
                              linestyle="dashed" if s == "미상" else "solid", boxstyle="round,pad=.28"))
            fig.chart_marks.artist(group, badge)
        y -= height/2
        ax.hlines(y, .01, .985, color=_RULE, linewidth=.5)
    ax.text(.01,-.95,caption,fontsize=9,color=_MUTED)
    return fig, f"**{title}** · {caption}\n\n" + _table(["항목","직전기","당기"], data)


def _percent_value(raw):
    try:
        value = float(str(raw).strip().removesuffix("%").strip())
    except ValueError:
        return None
    return value if 0 <= value <= 100 else None


def _chart_date(raw):
    try:
        return datetime.fromisoformat(str(raw)) if "-" in str(raw) else datetime.strptime(str(raw), "%Y%m%d")
    except ValueError:
        return None


def _vote_results(p):
    data = p["data"]
    results = data.get("results") or {}
    items = results.get("items") or []
    if not items or len(items) > 48:
        return
    title = f"{data.get('canonical_name') or p['subject']} · 주총 안건별 찬성률"
    labels, table, rates, outcomes = [], [], [], []
    for item in items:
        agenda = f"{item.get('number', '')} {item.get('agenda', '')}".strip()
        labels.append(fill(agenda, width=20))
        rate = _percent_value(item.get("approval_rate_voted"))
        rates.append(rate)
        outcome = item.get("passed") or "결과 미확인"
        if item.get("remark_contradicts_pass"):
            outcome += " (비고 확인)"
        outcomes.append(outcome)
        table.append((agenda, f"{rate:g}%" if rate is not None else "비율 미확인",
                      outcome, item.get("remark") or item.get("passed_raw") or "—"))
    no_rates = all(rate is None for rate in rates)
    if no_rates:
        title = f"{data.get('canonical_name') or p['subject']} · 주총 의결 결과"
    fig = _figure(title, max(3, 1.6 + .36 * sum(x.count("\n") + 1 for x in labels)), width=8.2)
    ax = fig.subplots()
    for i, (rate, outcome) in enumerate(zip(rates, outcomes)):
        group = fig.chart_marks.group(table[i][0], " · ".join(table[i]))
        if rate is not None:
            fig.chart_marks.artist(group, ax.barh(i, rate, height=.48, color=_PRIMARY)[0])
        value = f"{rate:g}%" if rate is not None else "비율 미확인"
        fig.chart_marks.artist(group, ax.text(102, i, f"{value} · {outcome}", va="center", fontsize=9))
    ax.set(yticks=range(len(labels)), yticklabels=labels, ylim=(len(labels)-.5, -.5),
           xlim=(0, 151), xticks=[0, 25, 50, 75, 100], xlabel="출석주식수 기준 찬성률 (%)")
    ax.grid(False, axis="y")
    if no_rates:
        ax.set(xlim=(95, 140), xticks=[], xlabel="출석주식수 기준 찬성률을 확인할 수 없는 공시")
    ref = data.get("result_reference") or {}
    source = ref.get("rcept_no") or ref.get("kind_acptno") or "접수번호 미확인"
    note = results.get("void_notice") or ""
    if note:
        fig.supxlabel("기타사항 공시 있음 · 아래 원문 참조", fontsize=9)
    return fig, f"**{title}** · 결과 공시 {source}\n\n" + _table(
        ["안건", "출석주식수 기준", "결과", "원문·비고"], table) + (f"\n\n기타사항: {note}" if note else "")


def _block_ownership(p):
    data = p["data"]
    holders = defaultdict(list)
    table = []
    for row in data.get("timeline") or []:
        reporter = row.get("reporter") or ""
        day = _chart_date(row.get("report_date"))
        rate = _percent_value(row.get("ownership_pct_raw"))
        if reporter and day and rate is not None:
            holders[reporter].append((day, rate))
        table.append((row.get("report_date"), reporter or "미상", f"{rate:g}%" if rate is not None else "미확인",
                      row.get("rcept_no") or "—"))
    if not holders or len(holders) > 16:
        return
    title = f"{data.get('canonical_name') or p['subject']} · 대량보유 보고 지분율"
    fig = _figure(title, max(3, 1.3 + 1.15*len(holders)))
    axes = fig.subplots(len(holders), sharex=True, sharey=True, squeeze=False).ravel()
    for ax, (holder, points) in zip(axes, sorted(holders.items())):
        xs, ys = zip(*sorted(points))
        ax.scatter(xs, ys, color=_PRIMARY, s=20)
        for day, rate in sorted(points):
            label = f"{holder} · {day:%Y-%m-%d}"
            group = fig.chart_marks.group(label, f"{label} · {rate:g}%")
            fig.chart_marks.point(group, ax, day, rate)
        ax.set_title(holder, loc="left", fontsize=10)
        ax.set_ylabel("%")
    locator = dates.AutoDateLocator(minticks=2, maxticks=6)
    axes[-1].xaxis.set_major_locator(locator)
    axes[-1].xaxis.set_major_formatter(dates.DateFormatter("%y-%m-%d"))
    axes[-1].set_xlabel("공시일 · 보고자 및 특별관계자 합산 · 조회 이력 최대 50건")
    return fig, f"**{title}** · 공시일별 보고값 · 조회 이력 최대 50건\n\n" + _table(
        ["공시일", "보고자", "지분율", "접수번호"], table)


def _treasury(p):
    data = p["data"]
    kinds = {
        "acquisition_result": ("취득 결과", "cumulative_shares"),
        "disposal_result": ("처분 결과", "actual_shares"),
        "acquisition_decision": ("취득 결정", None),
        "disposal_decision": ("처분 결정", None),
        "cancelation_decision": ("소각 결정", None),
    }
    events = []
    for row in data.get("events") or []:
        kind = kinds.get(row.get("event"))
        day = _chart_date(row.get("rcept_dt"))
        if kind and day:
            events.append((day, str(row.get("rcept_no") or ""), kind, row))
    if not events or len(events) > 60:
        return
    title = f"{data.get('canonical_name') or p['subject']} · 자사주 결정과 결과보고"
    fig = _figure(title, max(3.1, 1.6 + .32*len(events)), width=8)
    ax = fig.subplots()
    labels, table = [], []
    for i, (day, receipt, (label, field), row) in enumerate(sorted(events, key=lambda event: event[:2])):
        labels.append(f"{day.year} Q{(day.month-1)//3+1} · {day:%m/%d}  {label}")
        shares = row.get(field) if field else None
        if _number(shares) and shares >= 0:
            bar = ax.barh(i, shares, height=.48, color=_PRIMARY)[0]
            mark = ax.annotate(f"{shares:,}주", (shares, i), xytext=(4, 0), textcoords="offset points", va="center", fontsize=9)
            detail = f"{shares:,}주"
        else:
            detail = "수량 미확인" if field else "결정 공시"
            mark = ax.text(.01, i, ("◇ " if not field else "") + detail, transform=ax.get_yaxis_transform(),
                    va="center", color=_MUTED, fontsize=9)
        group = fig.chart_marks.group(labels[-1], f"{labels[-1]} · {detail} · {receipt}")
        fig.chart_marks.artist(group, mark)
        if _number(shares) and shares >= 0:
            fig.chart_marks.artist(group, bar)
        table.append((day.date().isoformat(), label, detail, receipt or "—"))
    ax.set(yticks=range(len(events)), yticklabels=labels, ylim=(len(events)-.5, -.5),
           xlabel="결과보고서 기재 주식 수 (주) · 분기는 공시일 기준")
    ax.margins(x=.35)
    ax.set_xlim(left=0)
    ax.xaxis.set_major_locator(mpl.ticker.MaxNLocator(nbins=5, integer=True))
    ax.xaxis.set_major_formatter(mpl.ticker.StrMethodFormatter("{x:,.0f}"))
    ax.grid(False, axis="y")
    return fig, f"**{title}** · 직접 취득·처분 결과보고 수량, 소각은 결정 단계 · 신탁 제외\n\n" + _table(
        ["공시일", "공시 유형", "결과 수량·단계", "접수번호"], table)


def _financial_amount(row, key):
    value = row.get(key)
    if row.get("functional_currency", "KRW") != "KRW" and not row.get("fx_rate_to_krw"):
        return nan
    return value if _number(value) else nan


def _amount_scale(values):
    peak = max((abs(v) for v in values if isfinite(v)), default=0)
    if peak >= 1e12:
        return 1e12, "조원"
    if peak >= 1e8:
        return 1e8, "억원"
    return 1, "원"


def _amount_cell(value):
    return f"{value:,.0f}" if isfinite(value) else "—"


def _fs_label(row):
    return {"CFS": "연결", "OFS": "별도"}.get(row.get("fs_div"), "기준 미확인")


def _quarterly_performance(p):
    data = p["data"]
    rows = data.get("quarterly") or []
    if not rows:
        return
    # Use fiscal ordinals to leave a true gap where a filing is missing.
    by_period = {int(r["fiscal_year"]) * 4 + int(r["fiscal_quarter"][1:]) - 1: r for r in rows}
    periods = range(min(by_period), max(by_period) + 1)
    labels, sales, margins, table = [], [], [], []
    for period in periods:
        year, offset = divmod(period, 4)
        labels.append(f"{year}\nQ{offset + 1}")
        row = by_period.get(period, {})
        standalone = row.get("basis") != "annual_cumulative"
        revenue = _financial_amount(row, "revenue_krw") if standalone else nan
        margin = row.get("operating_margin_pct")
        margin = margin if standalone and _number(margin) else nan
        sales.append(revenue)
        margins.append(margin)
        table.append((f"{year} Q{offset + 1}", row.get("period_end") or "—", _fs_label(row), _amount_cell(revenue),
                      f"{margin:.2f}%" if isfinite(margin) else "—"))
    if not any(isfinite(v) for v in sales + margins):
        return
    title = f"{data.get('canonical_name') or p['subject']} · 분기 매출과 영업이익률"
    fig = _figure(title, height=5.2)
    top, bottom = fig.subplots(2, sharex=True)
    scale, unit = _amount_scale(sales)
    x = range(len(labels))
    bars = top.bar(x, [v / scale for v in sales], color=_PRIMARY, width=.62)
    top.set_ylabel(f"매출 ({unit})")
    bottom.plot(x, margins, color=_PRIMARY, linewidth=2, marker="o", markersize=4)
    for i, values in enumerate(table):
        if isfinite(sales[i]) or isfinite(margins[i]):
            group = fig.chart_marks.group(values[0], f"{values[0]} · {values[2]} · 매출 {values[3]}원 · 영업이익률 {values[4]}")
            if isfinite(sales[i]):
                fig.chart_marks.artist(group, bars[i])
            if isfinite(margins[i]):
                fig.chart_marks.point(group, bottom, i, margins[i])
    bottom.set_ylabel("영업이익률 (%)")
    bottom.set_xticks(x, labels, fontsize=8)
    bottom.axhline(0, color=_REFERENCE, linewidth=.8)
    account_names = sorted({r.get("revenue_basis") or r.get("revenue_account_nm") or "기타 수익"
                            for r in rows if r.get("revenue_standard") is False})
    note = "사업연도 기준 · 분기 단독 실적 · DART 정기보고서"
    if account_names:
        note += " · 매출 대체 계정: " + ", ".join(account_names)
    bottom.set_xlabel(fill(note, 55), fontsize=9)
    return fig, f"**{title}** · {note}\n\n" + _table(["분기", "기간 말", "기준", "매출 (원)", "영업이익률"], table)


def _annual_finance(p, *, cash_flow):
    data = p["data"]
    rows = sorted(data.get("yearly") or [], key=lambda r: r["year"])
    if not rows:
        return
    if cash_flow:
        fields = ("fcf_krw", "dividend_paid_krw")
        names = ("잉여현금흐름", "현금배당 지급액")
    else:
        fields = ("cash_and_equivalents_krw", "total_debt_krw")
        names = ("현금및현금성자산", "총차입금")
    title = f"{data.get('canonical_name') or p['subject']} · {names[0]} · {names[1]}"
    def value(row, key):
        # The service may substitute an interim filing when the annual report is unavailable.
        if row.get("reprt_code") != "11011" or (cash_flow and row.get("period_basis") != "annual"):
            return nan
        return _financial_amount(row, key)
    amounts = [[value(row, key) for row in rows] for key in fields]
    flat = amounts[0] + amounts[1]
    if not any(isfinite(v) for v in flat):
        return
    scale, unit = _amount_scale(flat)
    fig = _figure(title, height=4.7)
    ax = fig.subplots()
    series_bars = []
    for j, vals in enumerate(amounts):
        bars = ax.bar([i + (j - .5) * .37 for i in range(len(rows))], [v / scale for v in vals],
                      width=.34, color=(_PRIMARY, _REFERENCE)[j], label=names[j])
        series_bars.append(bars)
        if len(rows) <= 4:
            ax.bar_label(bars, labels=[f"{v / scale:,.2f}".rstrip("0").rstrip(".") if isfinite(v) else ""
                                       for v in vals], padding=3, fontsize=8)
    ax.axhline(0, color=_REFERENCE, linewidth=.8)
    ax.margins(y=.17)
    ax.set_ylabel(f"금액 ({unit})")
    ax.legend(frameon=False, loc="lower left", bbox_to_anchor=(0, 1), ncols=2, fontsize=9)
    headers = ["사업연도", "기준", *(f"{name} (원)" for name in names)]
    labels, table = [], []
    if not cash_flow:
        headers.append("순현금 (원)")
    for i, row in enumerate(rows):
        year = str(row.get("fiscal_year") or row["year"])
        label = year
        cells = [year, _fs_label(row), *(_amount_cell(vals[i]) for vals in amounts)]
        if not cash_flow:
            net = value(row, "net_cash_krw")
            label += f"\n{net / scale:+,.2f}" if isfinite(net) else "\n—"
            cells.append(_amount_cell(net))
        labels.append(label)
        table.append(cells)
        if any(isfinite(vals[i]) for vals in amounts):
            detail = " · ".join(f"{h} {v}" for h, v in zip(headers, cells))
            group = fig.chart_marks.group(year, detail)
            for bars, vals in zip(series_bars, amounts):
                if isfinite(vals[i]):
                    fig.chart_marks.artist(group, bars[i])
    ax.set_xticks(range(len(rows)), labels, fontsize=8)
    note = ("연간 현금흐름표 · 지급연도 기준" if cash_flow
            else f"사업연도 말 · 연도 아래 숫자는 순현금 ({unit}) · 총차입금은 리스부채 제외")
    if cash_flow and any(r.get("is_financial_company") for r in rows):
        note += "\n금융업은 예수·대출 등 영업자금 변동이 포함돼 배당여력 해석에 주의"
    ax.set_xlabel(note, fontsize=9)
    return fig, f"**{title}** · {note}\n\n" + _table(headers, table)


def _transfer_stakes(table):
    """Read both sides from the same filing grid. Never normalise overlapping or missing stakes."""
    cols = ["".join(c.split()) for c in table.get("columns", [])]
    def index(prefix):
        matches = [i for i, c in enumerate(cols) if c.startswith(prefix)]
        if len(matches) != 1:
            raise ValueError("지분 표 열 미확인")
        return matches[0]
    name_i = index("성명")
    sides = [(index(s + "주식수"), index(s + "지분율")) for s in ("변경전", "변경후")]
    names = set()
    stakes = []
    for row in table.get("rows", []):
        name = row[name_i].strip()
        compact = "".join(name.split())
        if not name or compact in names or compact in {"-", "합계", "총계", "소계", "계"} or re.search(r"외\d+(?:명|인)", compact):
            raise ValueError("보유자 중복 또는 합산 행")
        names.add(compact)
        pair = []
        for share_i, pct_i in sides:
            text = row[pct_i].strip().removesuffix("%").strip()
            if text in {"-", "—", "–"} and row[share_i].strip() == "0":
                text = "0"
            value = Decimal(text)
            if not value.is_finite() or not 0 <= value <= 100:
                raise ValueError("지분율 범위")
            pair.append(value)
        stakes.append((name, *pair))
    if not stakes:
        raise ValueError("변경 전후 지분 표 없음")
    remaining = [100 - sum(r[i] for r in stakes) for i in (1, 2)]
    if min(remaining) < 0:
        raise ValueError("지분율 합계 초과")
    return [*stakes, ("그 밖의 지분", *remaining)]


def _latest_transfer_revision(events):
    """Select a correction only when every filing points to the same original agreement."""
    originals = [e for e in events if not e.get("amendment") and "정정" not in e.get("report_nm", "")]
    if len(originals) != 1:
        return None
    original = originals[0]
    filed = original["rcept_no"][:8]

    def parties(event):
        fields = event.get("fields") or {}
        return (fields.get("contract_on"), (fields.get("transferor") or {}).get("name"),
                (fields.get("transferee") or {}).get("name"))

    identity = parties(original)
    if not all(identity):
        return None
    for event in events:
        if event is original:
            continue
        source_date = (event.get("amendment") or {}).get("original_filed_date") or ""
        if source_date.replace("-", "") != filed or parties(event) != identity or event["rcept_no"] < original["rcept_no"]:
            return None
    return max(events, key=lambda e: e["rcept_no"])


def _transfer_donut(p):
    data = p["data"]
    receipt = p.get("chart_rcept_no")
    events = [e for e in data.get("control_events", []) if e.get("kind") == "transfer_contract"
              and (not receipt or e.get("rcept_no") == receipt)]
    if len(events) > 1:
        revision = _latest_transfer_revision(events)
        if revision:
            events = [revision]
    if len(events) > 1:
        return None, "계약 공시가 여러 건입니다. 표시할 원공시 또는 정정공시의 접수번호를 선택하세요.\n\n" + _table(
            ["접수번호", "공시명"], [(e["rcept_no"], e.get("report_nm", "")) for e in events])
    if not events:
        return None, "조회 범위에서 해당 주식양수도 계약 체결 공시를 찾지 못했습니다. 회사·기간·접수번호를 확인하세요."
    event = events[0]
    receipt = event["rcept_no"]
    link = f"[DART 원문 {receipt}](https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt})"
    try:
        stakes = _transfer_stakes(event.get("holders") or {})
    except (IndexError, ValueError, InvalidOperation):
        return None, f"전후 지분율을 중복 없이 확인할 수 없어 도넛을 생략했습니다. {link}"
    fields = event.get("fields") or {}
    expected = fields.get("expected_holder") or (fields.get("transferee") or {}).get("name") or ""
    def norm(s):
        return "".join(s.replace("㈜", "(주)").split())
    focus = next((i for i, r in enumerate(stakes[:-1]) if norm(r[0]) == norm(expected)), None)
    # Retain all original rows in the accompanying table; keep both rings readable for long registers.
    table = _table(["보유자", "계약 전", "계약 이행 예정"], [
        (n, f"{a}%", f"{b}%") for n, a, b in stakes])
    if len(stakes) > 7:
        ranked = sorted(range(len(stakes) - 1), key=lambda i: (i != focus, -max(stakes[i][1:])))
        selected = sorted(ranked[:5])
        shown = [stakes[i] for i in selected]
        shown.append(("그 밖의 지분", *(100 - sum(r[i] for r in shown) for i in (1, 2))))
        focus = selected.index(focus) if focus is not None else None
    else:
        shown = stakes
    palette = iter((_REFERENCE, "#A77C48", "#69779F", "#53806C", "#996981", "#68868D"))
    colors = [_PRIMARY if i == focus else next(palette) for i in range(len(shown)-1)] + [_RULE]
    company = data.get("canonical_name") or p["subject"]
    title = f"{company} · 주식양수도 계약 전후 지분율"
    fig = _figure(title, height=4.3 + .32*len(shown), width=8)
    grid = fig.add_gridspec(2, 2, height_ratios=[2.25, .40*len(shown)+.6])
    groups = [fig.chart_marks.group(n, f"{n} · 계약 전 {a}% → 계약 이행 예정 {b}% · {b-a:+}%p")
              for n, a, b in shown]
    for group, (_, before, after) in zip(fig.chart_marks.groups, shown):
        group["stakes"] = [f"{before}%", f"{after}%"]
    for i, heading in enumerate(("계약 전", "계약 이행 예정"), 1):
        ax = fig.add_subplot(grid[0, i-1])
        ax.set_title(heading, fontsize=12, weight="bold")
        wedges, _ = ax.pie([float(r[i]) for r in shown], colors=colors, startangle=90, counterclock=False,
               wedgeprops=dict(width=.27, edgecolor=_SURFACE, linewidth=.8))
        for group, wedge, stake in zip(groups, wedges, shown):
            if stake[i] > 0:
                fig.chart_marks.artist(group, wedge)
        pct = f"{shown[focus][i]}%" if focus is not None else " "
        caption = "변경예정 최대주주" if fields.get("expected_holder") else "양수인"
        ax.text(0, .06, pct, ha="center", color=_PRIMARY, fontsize=15, weight="bold", gid=f"stake-pct-{i}")
        ax.text(0, -.13, caption if focus is not None else " ", ha="center", fontsize=8,
                color=_MUTED, gid=f"stake-name-{i}")
    ax = fig.add_subplot(grid[1, :])
    ax.axis("off")
    ax.set(xlim=(0, 1), ylim=(-.4, len(shown)+.7))
    for x, label in ((.03, "보유자"), (.65, "계약 전"), (.95, "계약 이행 예정")):
        ax.text(x, len(shown), label, ha="left" if x < .1 else "right", fontsize=9, color=_MUTED)
    for j, (name, before, after) in enumerate(shown):
        y = len(shown) - j - 1
        ax.scatter(.04, y, color=colors[j], s=50)
        fig.chart_marks.point(groups[j], ax, .04, y)
        fig.chart_marks.artist(groups[j], ax.text(.065, y, fill(name, 25), va="center", fontsize=9))
        for x, value in ((.65, before), (.95, after)):
            fig.chart_marks.artist(groups[j], ax.text(x, y, f"{value}%", ha="right", va="center", fontsize=10))
    source = f"DART {receipt} · 공시상 예정 지분 · 그 밖의 지분은 잔여분"
    fig.supxlabel(source, fontsize=9, color=_MUTED)
    return fig, f"**{title}** · {source}\n\n{table}\n\n{link}"


def render_chart(name, payload):
    renderer = {"performance_analysis": _performance, "dividend_data": _dividend, "corp_gov_report": _governance,
                "shareholder_meeting_results": _vote_results, "ownership_structure": _block_ownership,
                "transfer_donut": _transfer_donut, "treasury_share": _treasury, "quarterly_performance": _quarterly_performance,
                "cash_flow": partial(_annual_finance, cash_flow=True),
                "cash_debt": partial(_annual_finance, cash_flow=False)}[name]
    with _LOCK, mpl.rc_context({"font.family": _font(), "text.parse_math": False, "axes.unicode_minus": False,
                                "figure.facecolor": _SURFACE, "axes.facecolor": _SURFACE, "text.color": _INK,
                                "axes.labelcolor": _MUTED, "xtick.color": _MUTED, "ytick.color": _MUTED,
                                "axes.edgecolor": _RULE, "axes.axisbelow": True, "axes.grid": True,
                                "axes.grid.axis": "y", "grid.color": _RULE, "grid.linewidth": .6,
                                "xtick.major.size": 0, "ytick.major.size": 0, "axes.spines.left": False,
                                "axes.spines.top": False, "axes.spines.right": False}):
        result = renderer(payload)
        if result is None:
            return None
        fig, summary = result
        if fig is None:
            return None, summary, None
        canvas = FigureCanvasAgg(fig)
        canvas.draw()
        marks = fig.chart_marks.export(canvas, fig._suptitle.get_text())
        fig.set_layout_engine("none")  # Keep the plotted marks and hit areas on the same final axes.
        out = StringIO()
        with mpl.rc_context({"svg.fonttype": "none"}):
            FigureCanvasSVG(fig).print_svg(out)
        return out.getvalue(), summary, marks
