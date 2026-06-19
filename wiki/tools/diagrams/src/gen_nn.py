# -*- coding: utf-8 -*-
"""OpenProxy MCP — 17 tool 공시 매핑을 심층신경망(DNN) 레이어 스타일 SVG로 생성."""
import html

# ---- 레이어 정의: (id, label) ----
L0 = [
    ("ty1", "정기보고서"),
    ("ty2", "주요사항보고서"),
    ("ty3", "거래소 수시공시"),
    ("ty4", "대량보유·지분 보고"),
    ("ty5", "공시 메타"),
]
L1 = [
    ("periodic", "사업·반기·분기보고서"),
    ("d_ts", "자기주식 취득·처분·신탁 결정"),
    ("d_issue", "유상증자·CB·BW·감자 결정"),
    ("d_merge", "합병·분할·주식교환 결정"),
    ("d_deal", "타법인주식 양수·양도 결정"),
    ("d_riskB", "회생·부도·영업정지·해산"),
    ("d_notice", "주주총회 소집공고"),
    ("d_result", "주주총회 결과(의결권행사)"),
    ("d_tsresult", "자기주식 결과보고서·소각"),
    ("d_divdec", "현금·현물배당 결정"),
    ("d_order", "단일판매·공급계약 체결"),
    ("d_gov", "기업지배구조보고서"),
    ("d_valueup", "기업가치제고계획(밸류업)"),
    ("d_riskI", "중대재해·횡령배임·생산중단"),
    ("d_lawsuit", "경영권분쟁소송 제기"),
    ("d_5pct", "5% 대량보유상황보고서"),
    ("d_ownchg", "최대주주 소유주식 변동신고"),
    ("d_proxy", "의결권 대리행사 권유(위임장)"),
    ("d_tender", "공개매수 신고서"),
    ("d_list", "전체 공시 목록"),
    ("d_recept", "rcept_no 메타"),
]
L2 = [
    ("da_fs", "재무제표(BS·IS·CF)"),
    ("da_audit", "감사의견"),
    ("da_divtable", "배당에 관한 사항"),
    ("da_majorholder", "최대주주·특수관계인"),
    ("da_shares", "주식총수·자기주식 현황"),
    ("da_tscond", "취득·처분·신탁 조건"),
    ("da_issuecond", "발행조건·잠재희석률"),
    ("da_mergeratio", "합병비율·외부평가"),
    ("da_dealterm", "상대방·금액·자산대비%"),
    ("da_riskB", "사유·영향(도산·정지)"),
    ("da_agenda", "안건·이사후보·보수한도"),
    ("da_advise", "안건 적법성·후보 평가"),
    ("da_vote", "안건별 가결/부결·찬반율"),
    ("da_tsactual", "실제 취득·처분·소각 수량"),
    ("da_dps", "DPS·배당총액·기준일"),
    ("da_contract", "계약금액·매출대비%"),
    ("da_gov15", "15개 지표 준수율"),
    ("da_commit", "commitment·이행"),
    ("da_riskI", "사상자·혐의금액"),
    ("da_lawsuit", "소송 단계·당사자"),
    ("da_coholder", "공동보유자 분해"),
    ("da_5dyn", "5% 동학(매집·목적)"),
    ("da_ownchg", "최대주주 지분 변동"),
    ("da_proxyrec", "위임장 권유 내역"),
    ("da_tendercond", "공개매수 조건"),
    ("da_index", "회사 식별·공시 인덱스"),
    ("da_viewer", "공시일·소스·뷰어 URL"),
]
L3 = [
    ("t_fin", "financial_metrics"),
    ("t_div", "dividend"),
    ("t_own", "ownership_structure"),
    ("t_tre", "treasury_share"),
    ("t_dil", "dilutive_issuance"),
    ("t_res", "corporate_restructuring"),
    ("t_dea", "corporate_deals"),
    ("t_rsk", "risk_events"),
    ("t_not", "shareholder_meeting_notice"),
    ("t_adv", "proxy_advise_before_meeting"),
    ("t_rslt", "shareholder_meeting_results"),
    ("t_ord", "order_contracts"),
    ("t_gov", "corp_gov_report"),
    ("t_val", "value_up"),
    ("t_pcn", "proxy_contest"),
    ("t_cmp", "company"),
    ("t_evd", "evidence"),
]

EDGES = [
    # L0 -> L1
    ("ty1", "periodic"),
    ("ty2", "d_ts"), ("ty2", "d_issue"), ("ty2", "d_merge"), ("ty2", "d_deal"), ("ty2", "d_riskB"),
    ("ty3", "d_notice"), ("ty3", "d_result"), ("ty3", "d_tsresult"), ("ty3", "d_divdec"),
    ("ty3", "d_order"), ("ty3", "d_gov"), ("ty3", "d_valueup"), ("ty3", "d_riskI"), ("ty3", "d_lawsuit"),
    ("ty4", "d_5pct"), ("ty4", "d_ownchg"), ("ty4", "d_proxy"), ("ty4", "d_tender"),
    ("ty5", "d_list"), ("ty5", "d_recept"),
    # L1 -> L2
    ("periodic", "da_fs"), ("periodic", "da_audit"), ("periodic", "da_divtable"),
    ("periodic", "da_majorholder"), ("periodic", "da_shares"),
    ("d_ts", "da_tscond"), ("d_issue", "da_issuecond"), ("d_merge", "da_mergeratio"),
    ("d_deal", "da_dealterm"), ("d_riskB", "da_riskB"),
    ("d_notice", "da_agenda"), ("d_notice", "da_advise"), ("d_result", "da_vote"),
    ("d_tsresult", "da_tsactual"), ("d_divdec", "da_dps"), ("d_order", "da_contract"),
    ("d_gov", "da_gov15"), ("d_valueup", "da_commit"), ("d_riskI", "da_riskI"),
    ("d_lawsuit", "da_lawsuit"),
    ("d_5pct", "da_coholder"), ("d_5pct", "da_5dyn"), ("d_ownchg", "da_ownchg"),
    ("d_proxy", "da_proxyrec"), ("d_tender", "da_tendercond"),
    ("d_list", "da_index"), ("d_recept", "da_viewer"),
    # L2 -> L3
    ("da_fs", "t_fin"), ("da_audit", "t_fin"),
    ("da_divtable", "t_div"), ("da_dps", "t_div"),
    ("da_majorholder", "t_own"), ("da_shares", "t_own"), ("da_coholder", "t_own"), ("da_ownchg", "t_own"),
    ("da_tscond", "t_tre"), ("da_tsactual", "t_tre"),
    ("da_issuecond", "t_dil"), ("da_mergeratio", "t_res"), ("da_dealterm", "t_dea"),
    ("da_riskB", "t_rsk"), ("da_riskI", "t_rsk"),
    ("da_agenda", "t_not"), ("da_advise", "t_adv"), ("da_vote", "t_rslt"),
    ("da_contract", "t_ord"), ("da_gov15", "t_gov"), ("da_commit", "t_val"),
    ("da_lawsuit", "t_pcn"), ("da_5dyn", "t_pcn"), ("da_proxyrec", "t_pcn"), ("da_tendercond", "t_pcn"),
    ("da_index", "t_cmp"), ("da_viewer", "t_evd"),
]

LAYERS = [L0, L1, L2, L3]
LAYER_TITLES = ["① 공시 타입", "② 공시 이름", "③ 추출 데이터", "④ Tool · 역할"]
LAYER_COLORS = ["#38bdf8", "#818cf8", "#fbbf24", "#34d399"]

# ---- 레이아웃 ----
LANE_W = 660
W = LANE_W * 4
TOP = 110
BOT = 50
SPACING = 44
MAXN = max(len(l) for l in LAYERS)
H_AREA = MAXN * SPACING
H = TOP + H_AREA + BOT
R = 9

def wrap(text, maxc=15):
    if all(ord(c) < 128 for c in text):  # ascii (tool 이름) 줄바꿈 안함
        return [text]
    out, cur = [], ""
    for ch in text:
        cur += ch
        if len(cur) >= maxc:
            out.append(cur); cur = ""
    if cur:
        out.append(cur)
    return out[:2]

pos = {}
for li, layer in enumerate(LAYERS):
    n = len(layer)
    cx = li * LANE_W + 34
    for i, (nid, _) in enumerate(layer):
        y = TOP + (i + 0.5) * H_AREA / n
        pos[nid] = (cx, y, li)

def esc(s):
    return html.escape(s, quote=True)

svg = []
svg.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{int(H)}" '
           f'viewBox="0 0 {W} {int(H)}" font-family="Malgun Gothic, sans-serif">')
svg.append(f'<rect width="{W}" height="{int(H)}" fill="#0b1220"/>')
# 그라데이션 글로우 정의
svg.append('<defs>')
for ci, c in enumerate(LAYER_COLORS):
    svg.append(f'<radialGradient id="g{ci}"><stop offset="0%" stop-color="{c}" stop-opacity="0.9"/>'
               f'<stop offset="100%" stop-color="{c}" stop-opacity="0"/></radialGradient>')
svg.append('</defs>')

# 제목
svg.append(f'<text x="40" y="46" fill="#ffffff" font-size="30" font-weight="700">'
           f'OpenProxy MCP — 공시 → 데이터 → Tool (심층 신경망 뷰)</text>')
for li, title in enumerate(LAYER_TITLES):
    cx = li * LANE_W + LANE_W / 2
    svg.append(f'<text x="{cx}" y="86" fill="{LAYER_COLORS[li]}" font-size="22" '
               f'font-weight="700" text-anchor="middle">{esc(title)} ({len(LAYERS[li])})</text>')

# 연결선 (노드/라벨 뒤에 깔기)
for a, b in EDGES:
    x1, y1, la = pos[a]
    x2, y2, lb = pos[b]
    sx = x1 + LANE_W - 58          # 라벨 영역 오른쪽 끝에서 출발
    ex = x2 - R - 2
    mx = (sx + ex) / 2
    col = LAYER_COLORS[la]
    svg.append(f'<path d="M{sx:.1f},{y1:.1f} C{mx:.1f},{y1:.1f} {mx:.1f},{y2:.1f} {ex:.1f},{y2:.1f}" '
               f'fill="none" stroke="{col}" stroke-width="1.1" stroke-opacity="0.22"/>')

# 노드 + 라벨
for li, layer in enumerate(LAYERS):
    col = LAYER_COLORS[li]
    for nid, label in layer:
        cx, cy, _ = pos[nid]
        # 글로우
        svg.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="17" fill="url(#g{li})"/>')
        # 뉴런
        svg.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{R}" fill="{col}" '
                   f'stroke="#0b1220" stroke-width="1.5"/>')
        lines = wrap(label)
        tx = cx + R + 9
        bold = ' font-weight="700"' if li == 3 else ''
        fsz = 15 if li != 3 else 14.5
        if len(lines) == 1:
            svg.append(f'<text x="{tx:.1f}" y="{cy+5:.1f}" fill="#dbe4ee" '
                       f'font-size="{fsz}"{bold}>{esc(lines[0])}</text>')
        else:
            svg.append(f'<text x="{tx:.1f}" y="{cy-2:.1f}" fill="#dbe4ee" font-size="{fsz}"{bold}>{esc(lines[0])}</text>')
            svg.append(f'<text x="{tx:.1f}" y="{cy+15:.1f}" fill="#dbe4ee" font-size="{fsz}"{bold}>{esc(lines[1])}</text>')

svg.append('</svg>')

out = "\n".join(svg)
with open("wiki/tools/diagrams/neural_net.svg", "w", encoding="utf-8") as f:
    f.write(out)
print(f"SVG written: {W}x{int(H)}")
