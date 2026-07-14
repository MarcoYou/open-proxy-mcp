# -*- coding: utf-8 -*-
"""law_lookup 자유질의 recall harness (held-out 손라벨, DART 0콜).
bridge 어휘를 일부러 피한 자연어 질의 → 정답 조문. bridge 자기참조가 아닌 진짜 recall 측정.
출력: label 검증 → recall@1/@10·MRR → 슬라이스(style/law) → 신호귀속 → miss분류."""
import re, json
from collections import Counter, defaultdict
from open_proxy_mcp.services.law_lookup import build_law_lookup_payload as B, load_index

# style: NL=자연어두루뭉술 / TERSE=제목일반어 / SCEN=우회시나리오 / PARA=bridge어휘회피 패러프레이즈
# (query, law_short, [정답 article_no...], style)
L = [
 # ── 이사 선임·해임·자격·책임 ──
 ("이사를 임기 도중에 내보내려면 어떻게 해야 해?", "상법", ["제385조"], "NL"),
 ("맘에 안 드는 이사 주주들이 강제로 그만두게 할 수 있어?", "상법", ["제385조"], "SCEN"),
 ("이사 뽑는 근거 조문이 뭐야?", "상법", ["제382조"], "NL"),
 ("소수주주가 원하는 사람을 이사로 넣을 수 있는 투표 방식", "상법", ["제382조의2"], "PARA"),
 ("이사가 회사 이익을 위해 성실히 일할 법적 의무", "상법", ["제382조의3"], "PARA"),
 ("이사가 회사랑 개인적으로 거래하려면 승인 받아야 해?", "상법", ["제398조"], "NL"),
 ("이사가 회사랑 같은 사업 하면 안 되는 규정", "상법", ["제397조"], "PARA"),
 ("이사가 회사 사업기회 가로채면 안 되는 조문", "상법", ["제397조의2"], "PARA"),
 ("이사 몇 명 이상 둬야 하고 임기는 몇 년까지야?", "상법", ["제383조"], "NL"),
 ("이사한테 얼마 줄지는 어디서 정해?", "상법", ["제388조"], "NL"),
 ("이사가 잘못해서 회사에 손해 끼치면 배상 책임", "상법", ["제399조"], "PARA"),
 ("주주가 회사 대신 이사한테 손배소송 거는 제도", "상법", ["제403조"], "PARA"),
 ("이사가 위법한 짓 하려는 걸 미리 막는 청구권", "상법", ["제402조"], "PARA"),
 # ── 감사·감사위원회 ──
 ("감사는 어떻게 선임해?", "상법", ["제409조"], "NL"),
 ("감사위원회는 어떻게 구성해?", "상법", ["제415조의2","제542조의12"], "NL"),
 ("상장사에서 감사위원 따로 뽑을 때 대주주 의결권 3%로 묶는 규정", "상법", ["제542조의12"], "SCEN"),
 # ── 주총 소집·주주권 ──
 ("주식 조금 가진 주주가 임시주총 열어달라고 요구하는 권리", "상법", ["제366조"], "PARA"),
 ("주주가 안건을 직접 올릴 수 있는 권리", "상법", ["제363조의2"], "PARA"),
 ("주총 소집 통지는 언제까지 어떻게 보내야 해?", "상법", ["제363조"], "NL"),
 ("주총 안 가고 종이로 의결권 행사하는 거", "상법", ["제368조의3"], "PARA"),
 ("컴퓨터로 주총 투표하는 제도", "상법", ["제368조의4"], "PARA"),
 ("남한테 의결권 대신 행사하게 맡기는 거", "상법", ["제368조"], "PARA"),
 # ── 정관·특별결의 ──
 ("정관 바꾸려면 주총에서 얼마나 찬성 받아야 해?", "상법", ["제434조","제433조"], "NL"),
 ("주총 특별결의 요건이 뭐야?", "상법", ["제434조"], "TERSE"),
 # ── 배당·자본 ──
 ("회사가 번 돈 주주한테 나눠주는 근거", "상법", ["제462조"], "PARA"),
 ("회계연도 중간에 배당하는 거", "상법", ["제462조의3"], "PARA"),
 ("배당을 현금 대신 주식으로 주는 거", "상법", ["제462조의2"], "PARA"),
 ("회사가 자기 주식 사들이는 근거 조문", "상법", ["제341조"], "NL"),
 ("사놓은 자기주식 없애버리는 거", "상법", ["제343조","제341조의4"], "PARA"),
 ("재무제표는 누가 언제 승인해?", "상법", ["제449조"], "NL"),
 # ── 신주·사채 ──
 ("회사가 새 주식 찍어내는 근거", "상법", ["제416조"], "PARA"),
 ("기존 주주가 새 주식 우선해서 받을 권리", "상법", ["제418조"], "PARA"),
 ("나중에 주식으로 바꿀 수 있는 회사채", "상법", ["제513조"], "PARA"),
 ("신주 살 권리가 붙은 회사채", "상법", ["제516조의2"], "PARA"),
 ("잉여금을 자본으로 넣어서 공짜 주식 주는 거", "상법", ["제461조"], "SCEN"),
 ("자본금을 줄이는 절차", "상법", ["제438조"], "NL"),
 # ── 구조개편 ──
 ("여러 주식을 하나로 합치는 거", "상법", ["제440조"], "TERSE"),
 ("주식 한 주를 여러 주로 쪼개는 거", "상법", ["제329조의2"], "PARA"),
 ("회사를 둘로 쪼개는 근거", "상법", ["제530조의2"], "PARA"),
 ("두 회사를 하나로 합치는 주총 승인", "상법", ["제522조","제174조"], "PARA"),
 ("규모 작은 합병은 주총 없이 이사회로 되는 거", "상법", ["제527조의3"], "SCEN"),
 ("완전자회사 만들려고 주식 전부 맞바꾸는 거", "상법", ["제360조의2"], "SCEN"),
 ("회사 영업을 통째로 넘기는 거 주총 승인 필요해?", "상법", ["제374조"], "NL"),
 # ── 소수주주·감독 ──
 ("주주가 회사 장부 들여다볼 수 있는 권리", "상법", ["제466조"], "PARA"),
 ("주총에서 회사 업무 조사할 사람 뽑는 거", "상법", ["제367조"], "SCEN"),
 ("주주명부 열람 청구", "상법", ["제396조"], "TERSE"),
 # ── 자본시장법 ──
 ("주식 5% 넘게 사면 신고해야 하는 규정", "자본시장법", ["제147조"], "SCEN"),
 ("경영권 노리고 장외에서 주식 공개적으로 사모으는 거", "자본시장법", ["제133조"], "PARA"),
 ("임원이나 대주주가 지분 변동 신고하는 거", "자본시장법", ["제173조"], "PARA"),
 ("내부자가 6개월 안에 사고팔아 번 차익 토해내는 거", "자본시장법", ["제172조"], "SCEN"),
 ("남의 의결권 모아달라고 권유할 때 규정", "자본시장법", ["제152조"], "PARA"),
 ("아직 공개 안 된 회사 정보로 주식 거래하면 안 되는 거", "자본시장법", ["제174조"], "PARA"),
 ("주가 인위적으로 띄우거나 조작하는 거 금지", "자본시장법", ["제176조"], "PARA"),
 ("상장사가 매년 내는 정기보고서", "자본시장법", ["제159조"], "PARA"),
 # ── 공정거래법 ──
 ("지주회사가 하면 안 되는 행위들", "공정거래법", ["제18조"], "NL"),
 ("계열사끼리 서로 지분 갖는 거 금지", "공정거래법", ["제21조"], "PARA"),
 ("돌고 도는 순환출자 금지", "공정거래법", ["제22조"], "PARA"),
 ("계열사 대규모 내부거래는 이사회 의결하고 공시해야 하는 거", "공정거래법", ["제26조"], "SCEN"),
 ("총수일가한테 부당하게 이익 몰아주는 거 금지", "공정거래법", ["제47조"], "PARA"),
 # ── 외부감사법 ──
 ("외부감사인은 어떻게 선임해?", "외부감사법", ["제10조"], "NL"),
 ("감사인을 금융당국이 주기적으로 지정하는 제도", "외부감사법", ["제11조"], "SCEN"),
 ("회사 내부회계관리제도 규정", "외부감사법", ["제8조"], "TERSE"),
]

def norm_no(s):
    """제385조 -> 385 ; 제382조의2 -> 382-2 ; 제542조의12 -> 542-12"""
    if not s: return ""
    s = s.replace("제","").replace("조","")
    return s.replace("의","-").strip()

idx = load_index()
have = {(a.get("law_short"), norm_no(a.get("article_no"))) for a in idx["articles"]}

# 1) 라벨 검증 — corpus에 실제 존재하는가
print("=== 라벨 검증(존재하지 않는 정답 조문) ===")
bad = 0
for q, law, arts, st in L:
    for a in arts:
        if (law, norm_no(a)) not in have:
            print(f"  MISSING: {law} {a}  ← \"{q[:30]}\""); bad += 1
print(f"라벨 {sum(len(x[2]) for x in L)}개 중 미존재 {bad}개\n" + ("→ 먼저 라벨 고쳐야 함\n" if bad else "→ 전부 존재 ✓\n"))
if bad: raise SystemExit

# 2) 러너
hit1 = hit10 = 0; mrr = 0.0
by_style = defaultdict(lambda: [0,0])   # style -> [hit10, tot]
by_law = defaultdict(lambda: [0,0])
sig_of_hit = Counter()                  # 맞힌 질의에서 정답을 잡은 신호
miss_fallback = Counter()               # miss 질의의 fallback type
misses = []
for q, law, arts, st in L:
    p = B(q, include_full_text=False, top_k=10)
    res = p["data"]["results"]
    gold = {(law, norm_no(a)) for a in arts}
    rank = None; hitsig = None
    for i, r in enumerate(res):
        if (r.get("law"), norm_no(r.get("article_no"))) in gold:
            rank = i+1; hitsig = tuple(r.get("signals") or []); break
    by_style[st][1]+=1; by_law[law][1]+=1
    if rank:
        hit10+=1; by_style[st][0]+=1; by_law[law][0]+=1; mrr += 1.0/rank
        if rank==1: hit1+=1
        sig_of_hit[hitsig]+=1
    else:
        fb=(p["data"].get("fallback") or {}).get("type") or p["status"]
        miss_fallback[fb]+=1
        top=[(r.get("article_no"), tuple(r.get('signals') or [])) for r in res[:3]]
        misses.append((st, law, arts, q, p["status"], fb, top))

N=len(L)
print(f"=== recall (N={N}) ===")
print(f"  recall@1 : {hit1}/{N} = {hit1/N:.0%}")
print(f"  recall@10: {hit10}/{N} = {hit10/N:.0%}")
print(f"  MRR      : {mrr/N:.3f}")
print("\n=== 슬라이스: style별 recall@10 ===")
for st,(h,t) in sorted(by_style.items()): print(f"  {st:5s}: {h}/{t} = {h/t:.0%}")
print("=== 슬라이스: 법령별 recall@10 ===")
for lw,(h,t) in sorted(by_law.items()): print(f"  {lw:8s}: {h}/{t} = {h/t:.0%}")
print("\n=== 맞힌 질의의 신호 귀속(어느 신호가 정답을 잡았나) ===")
for sig,c in sig_of_hit.most_common(): print(f"  {sig}: {c}")
print("=== miss 질의의 fallback 분포 ===")
for fb,c in miss_fallback.most_common(): print(f"  {fb}: {c}")
print(f"\n=== MISS 상세 ({len(misses)}건) ===")
for st,law,arts,q,stt,fb,top in misses:
    print(f"  [{st}] {law} 정답{arts} | \"{q}\" → status={stt}/{fb}")
    print(f"        top3: {top}")
