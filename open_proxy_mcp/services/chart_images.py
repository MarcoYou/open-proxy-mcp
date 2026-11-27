"""Report figures made from one completed tool response; no queries or result storage."""
from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from io import BytesIO
from math import isfinite, nan
from textwrap import fill
from threading import Lock

import matplotlib as mpl
from matplotlib import dates, font_manager
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

_LOCK = Lock()  # rc_context and font handling are shared across Agg figures.
_COLORS = ("#17658a", "#ad5529", "#36745b", "#78518d", "#776a24", "#595c76")


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
    fig.suptitle(fill(title, width=55), fontsize=17, fontweight="bold", color="#18364a")
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
    for i, (name, field) in enumerate(((tg, "cum_pct"), (bench, "bench_cum_pct"))):
        vals = [r[field] if _number(r.get(field)) else nan for r in rows]
        if not any(isfinite(v) for v in vals):
            continue
        ax = axes[-1] if i else axes[0]
        ax.plot(x, vals, color=_COLORS[i], linestyle="--" if i else "-", label=fill(name, 45),
                marker="." if len(rows) < 40 else None, linewidth=2)
    for ax in axes:
        ax.set_ylabel("누적수익률 (%)")
        ax.axhline(0, color="#a4b1b9", linewidth=.8)
        ax.legend(frameon=False, loc="best")
        locator = dates.AutoDateLocator(minticks=2, maxticks=6)
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(dates.DateFormatter("%m/%d" if (x[-1]-x[0]).days < 180 else "%Y-%m"))
    note = f"{per['start']} – {per['end']} · {basis} · OPM 성과 분석"
    if split:
        note += f" · 비교계열 기준일 {aligned['start']}"
    axes[-1].set_xlabel(note)
    sample = rows if len(rows) <= 6 else [rows[0], *rows[-5:]]
    def percent(v):
        return f"{v:,.2f}%" if _number(v) else "자료 없음"
    table = _table(["거래일", tg, bench], [(r["dd"], percent(r.get("cum_pct")), percent(r.get("bench_cum_pct"))) for r in sample])
    return fig, f"**{title}** · {note}\n\n{table}\n\n표는 최대 여섯 거래일을 발췌했습니다.\n자료: " + " · ".join(d.get("sources", []))


def _dividend(p):
    d = p["data"]
    annual = d.get("annual") or []
    if not annual or d.get("annual_failed"):
        return
    # Distinct stock classes and fiscal closings are independent observations.
    obs = {}
    for r in annual:
        period = str(r.get("stlm_dt") or r["bsns_year"])
        kind = (r.get("stock_knd_raw") or r.get("stock_kind") or "미구분").strip()
        key = period, kind
        if key in obs:
            return  # No unambiguous bar for this closing and class.
        obs[key] = r
    kinds = sorted({k for _, k in obs})
    periods = sorted({t for t, _ in obs})
    if len(kinds) > len(_COLORS) or len(periods) > 30:
        return
    title = f"{p['subject']} · 결산기별 주당배당금"
    fig = _figure(title)
    ax = fig.subplots()
    width = .8 / len(kinds)
    shown = []
    table_rows = []
    for j, kind in enumerate(kinds):
        x, y = [], []
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
        if y:
            bars = ax.bar(x, y, width=width*.92, color=_COLORS[j], label=kind)
            shown.extend(y)
            if len(obs) <= 16:
                ax.bar_label(bars, labels=[f"{v:,}" for v in y], fontsize=8, padding=2)
    if not shown:
        return
    ax.set_ylim(0, max(1, max(shown) * 1.18))
    ax.set_xticks(range(len(periods)), periods, rotation=45 if len(periods)>5 else 0, fontsize=8)
    ax.set_ylabel("주당배당금 (원)")
    ax.legend(frameon=False)
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
    for x, heading in ((.76,"직전기"),(.92,"당기")):
        ax.text(x,bottom+.85,heading,ha="center",weight="bold")
    y = bottom
    for label, height, (_, prior, curr) in zip(wrapped, heights, data):
        y -= height/2
        ax.text(.01,y,label,va="center",fontsize=9.5)
        for x,s in ((.76,prior),(.92,curr)):
            color = {"준수":"#d9eee8","미준수":"#f7dfc9"}.get(s,"#e6e9ec")
            ax.text(x,y,s,ha="center",va="center",fontsize=10,bbox=dict(facecolor=color,edgecolor="none",boxstyle="round,pad=.3"))
        y -= height/2
    ax.text(.01,-.95,caption,fontsize=9)
    return fig, f"**{title}** · {caption}\n\n" + _table(["항목","직전기","당기"], data)


def render_chart(name, payload):
    renderer = {"performance_analysis": _performance, "dividend_data": _dividend, "corp_gov_report": _governance}[name]
    with _LOCK, mpl.rc_context({"font.family": _font(), "text.parse_math": False, "axes.unicode_minus": False,
                                "axes.spines.top": False, "axes.spines.right": False}):
        result = renderer(payload)
        if result is None:
            return None
        fig, summary = result
        out = BytesIO()
        FigureCanvasAgg(fig).print_png(out)
        return out.getvalue(), summary
