#!/usr/bin/env python3
"""OPM 사용통계 LinkedIn 슬라이드 생성 (라이트 테마, 콤보 차트: 단일사용자 막대 + 인당요청 선)."""

# (date, weekday, users, requests_per_user, is_weekend, is_partial)
DAYS = [
    ("6/29", "월", 61, 25.7, False, False),
    ("6/30", "화", 135, 45.5, False, False),
    ("7/01", "수", 135, 46.9, False, False),
    ("7/02", "목", 144, 54.4, False, False),
    ("7/03", "금", 136, 47.2, False, False),
    ("7/04", "토", 95, 32.6, True, False),
    ("7/05", "일", 106, 40.7, True, False),
    ("7/06", "월", 150, 74.2, False, False),
    ("7/07", "화", 149, 74.6, False, False),
    ("7/08", "수", 163, 54.0, False, False),
    ("7/09", "목", 150, 48.3, False, False),
    ("7/10", "금", 164, 64.2, False, False),
    ("7/11", "토", 115, 42.5, True, False),
    ("7/12", "일", 82, 41.9, True, True),
]

# plot geometry
VW, VH = 1120, 208
mL, mR, top, base = 40, 34, 14, 168
plotW = VW - mL - mR
slot = plotW / len(DAYS)
BARW = 40
U_REF = 175.0   # users axis max (headroom over 164)
R_REF = 80.0    # rate axis max

def cx(i): return mL + (i + 0.5) * slot
def uy(v): return base - v / U_REF * (base - top)
def ry(v): return base - v / R_REF * (base - top)

# gridlines by users axis
grid = [50, 100, 150]
grid_svg = []
for g in grid:
    y = uy(g)
    grid_svg.append(f'<line x1="{mL}" y1="{y:.1f}" x2="{VW-mR}" y2="{y:.1f}" class="grid"/>')
    grid_svg.append(f'<text x="{mL-8}" y="{y+3.5:.1f}" class="ax ax-l">{g}</text>')
# right axis (rate) labels
for r in (40, 80):
    y = ry(r)
    grid_svg.append(f'<text x="{VW-mR+8}" y="{y+3.5:.1f}" class="ax ax-r">{r}</text>')

bars = []
for i, (_, _, u, _, wknd, part) in enumerate(DAYS):
    x = cx(i) - BARW / 2
    y = uy(u)
    h = base - y
    cls = "bar"
    if part: cls += " bar-part"
    elif wknd: cls += " bar-wk"
    bars.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{BARW}" height="{h:.1f}" rx="2.5" class="{cls}"/>')

# rate polyline + dots
pts = " ".join(f"{cx(i):.1f},{ry(d[3]):.1f}" for i, d in enumerate(DAYS))
dots = []
for i, d in enumerate(DAYS):
    x, y = cx(i), ry(d[3])
    part = d[5]
    dots.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{3.6 if not part else 3.2}" '
                f'class="{"dot dot-part" if part else "dot"}"/>')
    # value label above dot
    dots.append(f'<text x="{x:.1f}" y="{y-8:.1f}" class="rlab">{d[3]:.0f}</text>')

# x ticks (below plot)
ticks = []
for i, (dt, wd, *_rest) in enumerate(DAYS):
    wknd = DAYS[i][4]
    ticks.append(
        f'<div class="tick{" wk" if wknd else ""}">{dt}<span class="wd">{wd}</span></div>'
    )

svg = f'''<svg viewBox="0 0 {VW} {VH}" class="chart" preserveAspectRatio="none" role="img"
     aria-label="일별 단일 사용자와 인당 요청 추이">
  <line x1="{mL}" y1="{base}" x2="{VW-mR}" y2="{base}" class="axis"/>
  {"".join(grid_svg)}
  {"".join(bars)}
  <polyline points="{pts}" class="rate"/>
  {"".join(dots)}
</svg>'''

HTML = f'''<title>OPM 사용 리포트 — 런칭 2주</title>
<style>
  :root{{
    --paper:#F6F7F9; --card:#FFFFFF; --panel:#FCFCFD;
    --line:#E3E6EC; --line-2:#EEF0F4;
    --ink:#16202E; --muted:#606B7B; --muted-2:#98A1AD;
    --gold:#AE7A1E; --gold-soft:rgba(174,122,30,.10);
    --bar:#9DB0C8; --bar-wk:#C7D0DC;
    --font-sans:-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic","Noto Sans KR",sans-serif;
    --font-mono:"SF Mono","JetBrains Mono","Roboto Mono",ui-monospace,Menlo,monospace;
  }}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{
    background:#E9ECF1; font-family:var(--font-sans); color:var(--ink);
    min-height:100vh; display:flex; flex-direction:column; align-items:center;
    justify-content:center; gap:16px; padding:32px 16px;
  }}
  .stage-hint{{font-family:var(--font-mono);font-size:11px;letter-spacing:.14em;color:var(--muted-2);text-transform:uppercase}}
  .slide-wrap{{width:100%;overflow-x:auto;display:flex;justify-content:center}}
  .slide{{
    width:1200px; flex:0 0 1200px; aspect-ratio:16/9;
    background:linear-gradient(180deg,#FFFFFF 0%, var(--paper) 100%);
    border:1px solid var(--line); box-shadow:0 1px 0 rgba(20,32,46,.04);
    padding:46px 54px 38px; display:flex; flex-direction:column; justify-content:space-between;
    position:relative; font-variant-numeric:tabular-nums;
  }}
  .slide::before{{content:"";position:absolute;left:0;right:0;top:0;height:3px;
    background:linear-gradient(90deg,var(--gold) 0 22%, transparent 22%)}}
  header{{display:flex;justify-content:space-between;align-items:flex-start;gap:24px}}
  .eyebrow{{font-family:var(--font-mono);font-size:12.5px;letter-spacing:.22em;color:var(--gold);text-transform:uppercase;margin-bottom:13px}}
  h1{{font-size:31px;font-weight:700;letter-spacing:-.01em;line-height:1.1;text-wrap:balance;color:var(--ink)}}
  .sub{{color:var(--muted);font-size:14.5px;margin-top:8px}}
  .period{{text-align:right;font-family:var(--font-mono);font-size:12.5px;color:var(--muted);line-height:1.7;white-space:nowrap;padding-top:4px}}
  .period b{{color:var(--ink);font-weight:600}}

  .cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:15px;margin-top:26px}}
  .card{{background:var(--card);border:1px solid var(--line);border-top:2px solid var(--gold);
    padding:18px 18px 16px;display:flex;flex-direction:column;min-height:150px}}
  .card-label{{font-family:var(--font-mono);font-size:11px;letter-spacing:.15em;text-transform:uppercase;color:var(--muted);margin-bottom:auto}}
  .card-value{{font-family:var(--font-mono);font-weight:600;line-height:1;display:flex;align-items:baseline;gap:6px;margin-top:14px}}
  .card-value .num{{font-size:48px;letter-spacing:-.02em;color:var(--ink)}}
  .card-value .num.accent{{color:var(--gold)}}
  .card-value .unit{{font-size:15px;color:var(--muted);font-weight:500}}
  .card-desc{{font-size:12.5px;color:var(--muted);line-height:1.45;margin-top:11px}}

  .trend{{margin-top:20px;display:flex;flex-direction:column}}
  .trend-head{{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:6px}}
  .trend-head .t{{font-family:var(--font-mono);font-size:12px;letter-spacing:.15em;text-transform:uppercase;color:var(--muted)}}
  .legend{{display:flex;gap:18px;align-items:center;font-size:12px;color:var(--muted)}}
  .legend i{{display:inline-block;vertical-align:middle;margin-right:6px}}
  .legend .sw-bar{{width:11px;height:11px;border-radius:2px;background:var(--bar)}}
  .legend .sw-line{{width:16px;height:0;border-top:2.5px solid var(--gold);position:relative;top:-3px}}
  .legend .sw-line::after{{content:"";position:absolute;left:5px;top:-3px;width:6px;height:6px;border-radius:50%;background:var(--gold)}}

  .chart{{width:100%;height:200px;display:block}}
  .chart .axis{{stroke:var(--line);stroke-width:1}}
  .chart .grid{{stroke:var(--line-2);stroke-width:1}}
  .chart .ax{{font-family:var(--font-mono);font-size:10px;fill:var(--muted-2)}}
  .chart .ax-l{{text-anchor:end}}
  .chart .ax-r{{text-anchor:start;fill:var(--gold)}}
  .chart .bar{{fill:var(--bar)}}
  .chart .bar-wk{{fill:var(--bar-wk)}}
  .chart .bar-part{{fill:var(--bar-wk)}}
  .chart .rate{{fill:none;stroke:var(--gold);stroke-width:2.5;stroke-linejoin:round;stroke-linecap:round}}
  .chart .dot{{fill:#fff;stroke:var(--gold);stroke-width:2.2}}
  .chart .dot-part{{fill:var(--paper)}}
  .chart .rlab{{font-family:var(--font-mono);font-size:10px;fill:var(--gold);text-anchor:middle;font-weight:600}}

  .ticks{{display:grid;grid-template-columns:repeat(14,1fr);gap:0;margin-top:6px;padding:0 34px 0 40px}}
  .tick{{text-align:center;font-family:var(--font-mono);font-size:10px;color:var(--muted-2);line-height:1.3}}
  .tick .wd{{display:block;color:var(--muted-2);font-size:9px;opacity:.7}}
  .tick.wk{{color:#B08A3E}} .tick.wk .wd{{color:#B08A3E}}

  footer{{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-top:16px;padding-top:14px;border-top:1px solid var(--line)}}
  .ctx{{display:flex;gap:24px;flex-wrap:wrap}}
  .ctx .item{{font-size:12.5px;color:var(--muted)}}
  .ctx .item b{{font-family:var(--font-mono);color:var(--ink);font-weight:600}}
  .src{{font-size:11px;color:var(--muted-2);text-align:right;line-height:1.5;white-space:nowrap}}
</style>

<p class="stage-hint">↓ 이 슬라이드를 캡처해 LinkedIn에 업로드</p>
<div class="slide-wrap"><div class="slide">
  <header>
    <div>
      <div class="eyebrow">OPM · OpenProxy MCP</div>
      <h1>런칭 2주, 이렇게 쓰였습니다</h1>
      <div class="sub">DART 공시를 MCP로 — 한국 상장사 거버넌스 분석 도구</div>
    </div>
    <div class="period"><div><b>2026.06.29 – 07.12</b></div><div>외부 사용자 · KST</div></div>
  </header>

  <section class="cards">
    <div class="card"><div class="card-label">외부 사용자</div>
      <div class="card-value"><span class="num">281</span><span class="unit">명</span></div>
      <div class="card-desc">런칭 2주간 확보한 실사용자 (운영자 제외)</div></div>
    <div class="card"><div class="card-label">누적 요청</div>
      <div class="card-value"><span class="num">92,850</span><span class="unit">건</span></div>
      <div class="card-desc">공시·거버넌스 분석 tool 호출 수</div></div>
    <div class="card"><div class="card-label">재방문율</div>
      <div class="card-value"><span class="num accent">76</span><span class="unit">%</span></div>
      <div class="card-desc">이틀 이상 다시 찾은 사용자 비율 (213/281)</div></div>
    <div class="card"><div class="card-label">인당 체류</div>
      <div class="card-value"><span class="num accent">4.5</span><span class="unit">시간</span></div>
      <div class="card-desc">사용자 1인당 누적 사용 시간 (평균)</div></div>
  </section>

  <section class="trend">
    <div class="trend-head">
      <div class="t">일별 사용자 &amp; 사용 강도</div>
      <div class="legend">
        <span><i class="sw-bar"></i>일 단일 사용자 (명)</span>
        <span><i class="sw-line"></i>인당 요청 (건)</span>
      </div>
    </div>
    {svg}
    <div class="ticks">{"".join(ticks)}</div>
  </section>

  <footer>
    <div class="ctx">
      <div class="item">누적 <b>92,850</b> 요청</div>
      <div class="item">상위 <b>37%</b>가 요청의 90%</div>
      <div class="item">평균 응답 <b>655</b> ms</div>
    </div>
    <div class="src">출처: 자체 텔레메트리 (Supabase) · 운영자 키 제외 · 7/12 진행 중</div>
  </footer>
</div></div>'''

import sys
open(sys.argv[1], "w").write(HTML)
print("wrote", sys.argv[1])
