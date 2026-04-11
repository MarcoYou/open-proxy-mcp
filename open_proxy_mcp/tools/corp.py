"""기업 식별자 통합 조회 tool (corp_identifier)"""

import json
import asyncio

from open_proxy_mcp.dart.client import DartClient, DartClientError, get_dart_client
from open_proxy_mcp.tools.errors import tool_error, tool_not_found


def register_tools(mcp):

    @mcp.tool()
    async def corp_identifier(
        query: str,
        format: str = "md",
    ) -> str:
        """desc: 기업 통합 식별자 조회 — DART/NAVER 소스에서 가능한 모든 identifier + classification 수집.
        when: 종목코드, 회사명, 영문명, 약칭 등 어떤 입력으로도 기업의 전체 식별자를 한 번에 조회할 때. 동명기업 확인, 영문/약칭 매칭 실패 디버깅에도 사용. 다른 tool 호출 전 기업을 특정할 때 먼저 실행.
        rule: DART corpCode.xml(corp_code/stock_code) → DART company.json(법인번호/영문명/업종코드) → NAVER(업종명) 순서로 chain 조회. 동명기업 있으면 전체 목록 노출.
        ref: corp_manual, own_major, own_block, agm_personnel_xml, div_search
        """
        client = get_dart_client()

        # 1. DART corpCode.xml — 전체 매칭 목록
        all_corps = await client.lookup_corp_code_all(query)
        if not all_corps:
            return tool_not_found("기업", query)

        corp = all_corps[0]
        corp_code = corp["corp_code"]
        stock_code = corp.get("stock_code", "")

        # 2. DART company.json — 법인 상세정보
        company: dict = {}
        try:
            company = await client.get_company_info(corp_code)
        except DartClientError:
            pass

        # 3. NAVER — 업종명 (stock_code 있을 때만)
        naver: dict = {}
        if stock_code:
            try:
                naver = await client.get_naver_corp_profile(stock_code)
            except Exception:
                pass

        # corp_cls → 시장 명칭
        cls_map = {"Y": "KOSPI (유가증권)", "K": "KOSDAQ (코스닥)", "N": "KONEX (코넥스)", "E": "비상장"}
        corp_cls = company.get("corp_cls", "")
        market = cls_map.get(corp_cls, corp_cls or "미상")

        # 결산월 포맷
        acc_mt = company.get("acc_mt", "")
        fiscal = f"{acc_mt}월" if acc_mt else "-"

        # 법인번호 포맷
        jurir = company.get("jurir_no", "")
        bizr = company.get("bizr_no", "")

        if format == "json":
            return json.dumps({
                "corp_code": corp_code,
                "stock_code": stock_code,
                "corp_name": company.get("corp_name") or corp["corp_name"],
                "corp_name_eng": company.get("corp_name_eng", ""),
                "stock_name": company.get("stock_name", ""),
                "corp_cls": corp_cls,
                "market": market,
                "sector_name": naver.get("sector_name", ""),
                "sector_code": naver.get("sector_code", ""),
                "induty_code": company.get("induty_code", ""),
                "ceo_nm": company.get("ceo_nm", ""),
                "acc_mt": acc_mt,
                "jurir_no": jurir,
                "bizr_no": bizr,
                "est_dt": company.get("est_dt", ""),
                "adres": company.get("adres", ""),
                "hm_url": company.get("hm_url", ""),
                "duplicates": [
                    {"corp_code": c["corp_code"], "stock_code": c["stock_code"],
                     "corp_name": c["corp_name"], "modify_date": c["modify_date"]}
                    for c in all_corps
                ],
            }, ensure_ascii=False, indent=2)

        # Markdown
        corp_name = company.get("corp_name") or corp["corp_name"]
        corp_name_eng = company.get("corp_name_eng", "")
        sector_name = naver.get("sector_name", "-")
        ceo = company.get("ceo_nm", "-")
        adres = company.get("adres", "-")
        hm_url = company.get("hm_url", "")
        est_dt = company.get("est_dt", "")
        est_fmt = f"{est_dt[:4]}-{est_dt[4:6]}-{est_dt[6:8]}" if len(est_dt) == 8 else est_dt

        lines = [f"# {corp_name}"]
        if corp_name_eng:
            lines.append(f"*{corp_name_eng}*")
        lines.append("")

        lines.append("## 식별자")
        lines.append(f"| 항목 | 값 |")
        lines.append(f"|------|----|")
        lines.append(f"| 종목코드 (KRX) | `{stock_code}` |" if stock_code else "| 종목코드 | 비상장 |")
        lines.append(f"| DART 기업코드 | `{corp_code}` |")
        lines.append(f"| 법인등록번호 | `{jurir}` |" if jurir else "| 법인등록번호 | - |")
        lines.append(f"| 사업자번호 | `{bizr}` |" if bizr else "| 사업자번호 | - |")
        lines.append("")

        lines.append("## 분류")
        lines.append(f"| 항목 | 값 |")
        lines.append(f"|------|----|")
        lines.append(f"| 시장 | {market} |")
        lines.append(f"| 업종 | {sector_name} |")
        lines.append(f"| 업종코드 (DART) | {company.get('induty_code', '-')} |")
        lines.append(f"| 결산월 | {fiscal} |")
        lines.append("")

        lines.append("## 기본정보")
        lines.append(f"| 항목 | 값 |")
        lines.append(f"|------|----|")
        lines.append(f"| 대표이사 | {ceo} |")
        lines.append(f"| 설립일 | {est_fmt or '-'} |")
        lines.append(f"| 주소 | {adres} |")
        if hm_url:
            lines.append(f"| 홈페이지 | {hm_url} |")
        lines.append("")

        # 동명기업 있으면 전체 목록 표시
        if len(all_corps) > 1:
            lines.append("## ⚠️ 동명기업 — 전체 목록")
            lines.append("*아래 중 하나를 특정하려면 종목코드나 corp_code를 직접 입력하세요.*")
            lines.append("")
            lines.append("| corp_code | stock_code | 회사명 | modify_date |")
            lines.append("|-----------|------------|--------|-------------|")
            for c in all_corps:
                marker = " ← **현재 선택**" if c["corp_code"] == corp_code else ""
                lines.append(f"| `{c['corp_code']}` | `{c['stock_code']}` | {c['corp_name']} | {c['modify_date']}{marker} |")

        return "\n".join(lines)

    @mcp.tool()
    async def corp_manual() -> str:
        """desc: corp_identifier 사용 가이드 — 입력 타입별 동작, 동명기업 처리, alias 목록.
        when: corp_identifier 사용법이 불명확하거나, 기업 검색이 실패할 때.
        rule: 이 tool 자체는 DART API를 호출하지 않음.
        ref: corp_identifier
        """
        return """# corp_resolve 사용 가이드

## 지원하는 입력 타입

| 입력 | 예시 | 동작 |
|------|------|------|
| 종목코드 (6자리) | `005930` | 정확 매치 |
| DART corp_code (8자리) | `00126380` | 정확 매치 |
| 한글 회사명 | `삼성전자` | 정확→부분 매치 |
| 약칭/브랜드명 | `TKG휴켐스`, `KT&G` | alias dict → DART 정식명 |
| 영문명 | `LS ELECTRIC` | alias dict → `엘에스일렉트릭` |
| 법인격 포함 | `삼성전자㈜` | 법인격 strip 후 매치 |

## 알려진 alias 매핑

| 입력 | DART 정식명 |
|------|------------|
| LS ELECTRIC | 엘에스일렉트릭 |
| SK바이오팜 | 에스케이바이오팜 |
| KT&G | 케이티앤지 |
| TKG휴켐스 | 티케이지휴켐스 |

## 동명기업 처리

동명기업이 있으면 `modify_date` 최신 + 상장 기업 우선으로 첫 번째 선택.
결과 하단에 전체 목록 표시. 특정 법인을 선택하려면 종목코드나 corp_code 직접 입력.

예: `미래에셋증권` → 2개 (006800이 최신 선택)

## 데이터 소스

| 소스 | 제공 데이터 |
|------|------------|
| DART corpCode.xml | corp_code, stock_code, corp_name |
| DART company.json | 영문명, 법인번호, 사업자번호, corp_cls, 대표이사, 결산월, 업종코드 |
| NAVER 금융 | 업종명 (한국어) |

업종명 조회는 NAVER 웹 스크래핑으로 4초 추가 소요.
"""
