"""Step 1: OpenDART API 동작 확인 — 주주총회 소집공고 검색"""

import urllib.request
import urllib.parse
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")

API_KEY = "33ac18b8b8086ec4c79861406ca7594efae88ae0"

# 공시검색 API: 2026년 1~3월 주주총회 소집공고
# 공시유형별 테스트: 각 유형 코드에서 소집공고 찾기
for ty_code, ty_name in [("A","정기공시"), ("B","주요사항"), ("C","발행공시"), ("D","지분공시"), ("E","기타공시"), ("F","외부감사"), ("G","펀드공시"), ("H","자산유동화"), ("I","거래소공시")]:
    p = urllib.parse.urlencode({
        "crtfc_key": API_KEY,
        "bgn_de": "20260201",
        "end_de": "20260319",
        "pblntf_ty": ty_code,
        "page_count": "100",
    })
    u = f"https://opendart.fss.or.kr/api/list.json?{p}"
    req = urllib.request.Request(u)
    with urllib.request.urlopen(req) as response:
        d = json.loads(response.read().decode("utf-8"))
    items = d.get("list", [])
    found = [x for x in items if "소집" in x.get("report_nm", "")]
    total = d.get("total_count", 0)
    if found:
        print(f"\n=== {ty_code} ({ty_name}) — 총 {total}건, 소집 {len(found)}건 ===")
        for item in found[:3]:
            print(f"  {item['corp_name']} | {item['report_nm']} | {item['rcept_dt']}")
    else:
        print(f"{ty_code} ({ty_name}): 총 {total}건, 소집 0건")

print("\n완료")
params = urllib.parse.urlencode({
    "crtfc_key": API_KEY,
    "bgn_de": "20260201",
    "end_de": "20260319",
    "page_count": "10",
})

url = f"https://opendart.fss.or.kr/api/list.json?{params}"

print(f"요청 URL: {url[:80]}...")
print()

try:
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read().decode("utf-8"))

    print(f"상태: {data.get('status')}")
    print(f"메시지: {data.get('message')}")
    print(f"총 건수: {data.get('total_count', 'N/A')}")
    print()

    if data.get("list"):
        # 주주총회소집 관련만 필터
        filtered = [item for item in data["list"] if "소집" in item.get("report_nm", "")]
        print(f"'소집' 포함 건수: {len(filtered)}")
        print()
        for i, item in enumerate(filtered[:10]):
            print(f"--- {i+1} ---")
            print(f"  회사: {item.get('corp_name')}")
            print(f"  보고서명: {item.get('report_nm')}")
            print(f"  접수일: {item.get('rcept_dt')}")
            print(f"  접수번호: {item.get('rcept_no')}")
            print(f"  공시유형: {item.get('pblntf_ty')}")
            print(f"  공시상세: {item.get('pblntf_detail_ty')}")
            print()
    else:
        print("결과 없음")
        print(f"전체 응답: {json.dumps(data, ensure_ascii=False, indent=2)}")

except Exception as e:
    print(f"에러: {e}")
