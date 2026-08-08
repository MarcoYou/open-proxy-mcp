"""주총 1호 안건 (재무제표 승인) 본문에서 잠정 재무제표 raw 추출.

DART 주총소집공고 본문에 첨부되는 잠정 재무제표:
- 사업보고서 제출 전 회사 자가 공시
- DART API fnlttSinglAcnt (사업보고서 확정치)와 source 다름 — 잠정치
- 4 quadrant: consolidated/separate × balance_sheet/income_statement

Layer: data tool (parsing + computation, 판단 X). Action tool (proxy_advise)에서 정량 metric은 별도
helper (`extract_metrics`)로 추출하여 facts evidence 활용.

이전 `tools/parser.py:parse_financials_xml` 본체를 통째로 가져옴 (parser.py 의존성 제거).
구 `agm_first_agenda_fy.py` 정규식 텍스트 파서 폐기 (archive에 v1 보존).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
import warnings as _warnings

logger = logging.getLogger(__name__)

# bs4 parser
try:
    import lxml  # noqa: F401
    _BS4_PARSER = "lxml"
except ImportError:
    _BS4_PARSER = "html.parser"

_warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# ── 재무제표 식별 regex ──
_FS_BALANCE_SHEET = re.compile(r'재무상태표|대차대조표')
_FS_INCOME_STMT = re.compile(r'손익계산서|포괄손익')
_FS_CONSOLIDATED = re.compile(r'연결')
_FS_SEPARATE = re.compile(r'별도|개별')
_FS_UNIT = re.compile(r'\(단위\s*[:：]?\s*(.+?)\)')
_FS_PERIOD = re.compile(r'(제\s*\d+\s*\(?\s*(?:당|전)\s*\)?\s*기|(?:20)?\d{2,4}\s*년)')


def parse_provisional_financial_statement(html: str) -> dict[str, Any]:
    """HTML에서 잠정 재무제표 (재무상태표 + 손익계산서) 4 quadrant 구조화 추출.

    목적사항별 기재사항 > 재무제표 영역에서:
    - 연결/별도 구분
    - 재무상태표 / 손익계산서 테이블 추출
    - 단위, 기간 라벨 메타데이터 포함

    Returns:
        {
          "consolidated": {"balance_sheet": {...} | None, "income_statement": {...} | None},
          "separate": {"balance_sheet": {...} | None, "income_statement": {...} | None}
        }
    """
    soup = BeautifulSoup(html, _BS4_PARSER)

    # 목적사항별 기재사항 섹션 찾기
    detail_section = None
    for el in soup.find_all('title'):
        if '목적사항별' in (el.get_text() or ''):
            detail_section = el.parent
            break

    if not detail_section:
        logger.warning("재무제표 파싱: 목적사항별 기재사항 섹션을 찾을 수 없음")
        return _empty_financial_result()

    # 재무제표 library 찾기 — 카테고리 title 또는 본문에서 재무제표 키워드
    fs_container = None
    for lib in detail_section.find_all('library'):
        container = lib.find('section-3') or lib
        title_el = container.find('title')
        if title_el:
            title_text = re.sub(r'\s+', '', title_el.get_text())
            if '재무제표' in title_text or '재무상태표' in title_text or '대차대조표' in title_text:
                fs_container = container
                break
        text = re.sub(r'\s+', '', lib.get_text()[:500])
        if _FS_BALANCE_SHEET.search(text) or '재무제표' in text:
            fs_container = container
            break

    # fallback: library 없이 section 직계 자식에 재무제표가 있는 경우
    if not fs_container:
        section_text = re.sub(r'\s+', '', detail_section.get_text()[:1000])
        if '재무제표' in section_text or '재무상태표' in section_text:
            direct_tables = [t for t in detail_section.find_all('table', recursive=False)]
            if direct_tables:
                fs_container = detail_section
                logger.info("재무제표 파싱: library 없이 section에서 직접 발견")

    if not fs_container:
        logger.warning("재무제표 파싱: 재무제표 library를 찾을 수 없음")
        return _empty_financial_result()

    # 데이터 테이블 수집 — 행 5개 이상, 첫 행에 '과목' 포함
    result = {
        "consolidated": {"balance_sheet": None, "income_statement": None},
        "separate": {"balance_sheet": None, "income_statement": None},
    }

    # 현재 컨텍스트 추적
    fs_text = re.sub(r'\s+', '', fs_container.get_text()[:3000])
    has_consolidated = bool(_FS_CONSOLIDATED.search(fs_text))
    is_consolidated = has_consolidated  # "연결" 없으면 기본값 = 별도
    current_stmt_type = None  # 'balance_sheet' or 'income_statement'

    for child in fs_container.descendants:
        if not hasattr(child, 'name') or not child.name:
            continue

        text = child.get_text().strip()

        # <p> 헤딩으로 컨텍스트 갱신
        if child.name == 'p' and text:
            text_clean = re.sub(r'\s+', '', text)
            has_cons = bool(_FS_CONSOLIDATED.search(text_clean))
            has_sepa = bool(_FS_SEPARATE.search(text_clean))
            if has_cons and has_sepa:
                cons_pos = _FS_CONSOLIDATED.search(text_clean).start()
                sepa_pos = _FS_SEPARATE.search(text_clean).start()
                is_consolidated = cons_pos < sepa_pos
            elif has_sepa:
                is_consolidated = False
            elif has_cons:
                is_consolidated = True

            if re.search(r'현금흐름', text_clean):
                current_stmt_type = None
            elif re.search(r'자본변동', text_clean):
                current_stmt_type = None
            elif re.search(r'이익잉여금처분|결손금처리', text_clean):
                current_stmt_type = None
            elif _FS_BALANCE_SHEET.search(text_clean):
                current_stmt_type = 'balance_sheet'
            elif _FS_INCOME_STMT.search(text_clean):
                current_stmt_type = 'income_statement'
            continue

        # 제목 테이블에서도 컨텍스트 갱신
        if child.name == 'table':
            rows = child.find_all('tr')
            if len(rows) <= 4:
                table_text = child.get_text()
                table_text_clean = re.sub(r'\s+', '', table_text)
                if _FS_SEPARATE.search(table_text):
                    is_consolidated = False
                elif _FS_CONSOLIDATED.search(table_text):
                    is_consolidated = True
                if re.search(r'현금흐름|자본변동|이익잉여금처분|결손금처리', table_text_clean):
                    current_stmt_type = None
                elif _FS_BALANCE_SHEET.search(table_text_clean):
                    current_stmt_type = 'balance_sheet'
                    if not _FS_CONSOLIDATED.search(table_text) and not _FS_SEPARATE.search(table_text):
                        scope_check = "consolidated" if is_consolidated else "separate"
                        if result[scope_check]["balance_sheet"] is not None:
                            is_consolidated = False
                elif _FS_INCOME_STMT.search(table_text_clean):
                    current_stmt_type = 'income_statement'
                    if not _FS_CONSOLIDATED.search(table_text) and not _FS_SEPARATE.search(table_text):
                        scope_check = "consolidated" if is_consolidated else "separate"
                        if result[scope_check]["income_statement"] is not None:
                            is_consolidated = False
                continue

            # 데이터 테이블 판별
            first_cells = [c.get_text().strip() for c in rows[0].find_all(['td', 'th'])]
            first_cells_clean = [re.sub(r'\s+', '', c) for c in first_cells]
            is_data_table = any(
                ('과' in c and '목' in c) or ('구' in c and '분' in c)
                for c in first_cells_clean
            )
            if not is_data_table and len(first_cells_clean) >= 2:
                has_period = any(
                    re.match(r'제?\d+기', c) or c in ('당기', '전기', '당기말', '전기말')
                    for c in first_cells_clean
                )
                if has_period:
                    is_data_table = True
            if not is_data_table:
                continue

            if current_stmt_type is None:
                current_stmt_type = _infer_statement_type(child)
            if current_stmt_type is None:
                continue

            scope = "consolidated" if is_consolidated else "separate"
            if result[scope][current_stmt_type] is not None:
                other = "income_statement" if current_stmt_type == "balance_sheet" else "balance_sheet"
                if result[scope][other] is None:
                    inferred = _infer_statement_type(child)
                    if inferred and inferred == other:
                        current_stmt_type = other
                    else:
                        continue
                else:
                    is_consolidated = not is_consolidated
                    scope = "consolidated" if is_consolidated else "separate"
                    current_stmt_type = _infer_statement_type(child)
                    if current_stmt_type is None or result[scope][current_stmt_type] is not None:
                        continue

            unit = _extract_unit_from_siblings(child)
            header_cells_raw = rows[0].find_all(['td', 'th'])
            expanded_header = []
            for c in header_cells_raw:
                val = c.get_text().strip()
                colspan = int(c.get('colspan', 1) or 1)
                expanded_header.append(val)
                for _ in range(colspan - 1):
                    expanded_header.append('')
            actual_cols = len(expanded_header)
            period_labels = _extract_period_labels(expanded_header)

            data_rows = []
            for row in rows[1:]:
                cells = row.find_all(['td', 'th'])
                expanded = []
                for c in cells:
                    val = c.get_text().strip().replace('\n', ' ')
                    colspan = int(c.get('colspan', 1) or 1)
                    expanded.append(val)
                    for _ in range(colspan - 1):
                        expanded.append('')
                while len(expanded) < actual_cols:
                    expanded.append('')
                data_rows.append(expanded[:actual_cols])

            columns = _build_column_meta(expanded_header)
            has_note = "note" in columns
            normalized = _normalize_financial_rows(columns, data_rows)

            if has_note:
                out_columns = ["account", "note", "current", "prior"]
            else:
                out_columns = ["account", "current", "prior"]
                if normalized and len(normalized[0]) == 4:
                    normalized = [[r[0], r[2], r[3]] for r in normalized]

            result[scope][current_stmt_type] = {
                "unit": unit,
                "period_labels": period_labels,
                "columns": out_columns,
                "column_count": len(out_columns),
                "rows": normalized,
                "row_count": len(normalized),
            }

    return result


# DART 소집공고는 절 제목에 **표준 영문명**을 함께 단다 — 회사가 한글 제목을 어떻게 쓰든
# `<TITLE ENG="□ Approval of separate financial statements">` 는 고정이다(실측 97% 보유).
# 재무제표를 못 낼 때 이게 있으면 「우리가 못 읽은 것」, 없으면 「원문에 그 절이 없는 것」이다.
_FS_SECTION_ENG = "Approval of separate financial statements"
_TITLE_ENG_RE = re.compile(r"""<TITLE[^>]*\bENG\s*=\s*["']([^"']*)""", re.I)
# 상법 §449의2 — 이사회가 승인하면 주총에선 보고사항이라 승인 안건 자체가 없다.
_FS_BOARD_APPROVED_RE = re.compile(
    r"이사회(?:의\s*결의)?로?\s*승인[^.]{0,40}보고\s*안건|보고\s*사항으로\s*전환|449조의2")
# 임시주총엔 재무제표 승인 안건이 없다(정기주총 안건). 공고 머리의 「(제N기 임시)」로 가른다.
_EGM_RE = re.compile(r"주주총회\s*소집공고\s*\(?\s*제?\s*\d*\s*기?\s*임시|임시\s*주주총회\s*소집공고")
# 실제 재무제표 표가 원문에 있는지 — 표 제목 + 기수 + 연도. DART 표는 자간을 벌리므로
# 「재 무 상 태 표」·「제 12 기」처럼 띄어 쓴다(공백을 허용하지 않으면 통째로 놓친다).
_FS_REAL_TABLE_RE = re.compile(
    r"(?:대\s*차\s*대\s*조\s*표|재\s*무\s*상\s*태\s*표|손\s*익\s*계\s*산\s*서)"
    r"[^가-힣]{0,40}제\s*\d+\s*기[^가-힣]{0,60}\d{4}\s*년")


def classify_provisional_fs_absence(html: str) -> dict[str, Any]:
    """잠정 재무제표를 못 냈을 때 **왜 못 냈는지**를 가른다.

    종전 렌더는 전부 「1호 안건 본문 비표준 형식」이라 단정했는데, 실측에서 그 원인이 아닌
    경우가 대부분이었다 — 확인하지 않은 원인을 확정형으로 말한 셈이다.

    소집공고의 「목적사항별 기재사항」은 `□` 항목으로 서식화돼 있고(한글 15종 고정),
    각 `□` 제목에는 표준 영문명이 **빠짐없이** 함께 붙는다(실측 4,716개 중 결측 0). 즉
    영문은 한글의 짝일 뿐 부가 정보가 아니다 — 어느 쪽으로 앵커해도 결과는 같다.

    신호는 **한 방향으로만** 쓴다.
      · `□` 항목이 **있는데** 값이 없다 → 서식대로 적었는데 우리가 못 읽었다. 반례 없음.
      · 항목이 **없다**고 원문에 없다고 단정하면 안 된다. 회사가 `□` 서식을 쓰지 않고
        「제3-1호 의안: 사내이사 OOO 선임의 건」처럼 본문에 풀어 쓰는 경우가 있고, 그때도
        실제 표는 실려 있다(같은 문서에 다른 `□` 항목은 붙어 있다). 그래서 표 자체가
        보이면 미탐으로 잡고, 아무 근거도 없으면 **「확인하지 못했다」**까지만 말한다.
    """
    has_section = any(_FS_SECTION_ENG in (m.group(1) or "")
                      for m in _TITLE_ENG_RE.finditer(html or ""))
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or ""))
    if has_section or _FS_REAL_TABLE_RE.search(text):
        return {"absence_kind": "extraction_failed",
                "absence_note": "소집공고에 재무제표가 실려 있으나 표를 읽어내지 못했습니다 "
                                "— 원문을 직접 확인하세요."}
    m = _FS_BOARD_APPROVED_RE.search(text)
    if m:
        return {"absence_kind": "not_disclosed",
                "absence_note": "이사회가 재무제표를 승인해 주주총회에서는 보고사항입니다"
                                "(상법 제449조의2) — "
                                f"「…{text[max(0, m.start() - 50):m.end() + 30].strip()}…」"}
    if _EGM_RE.search(text[:4000]):
        return {"absence_kind": "not_disclosed",
                "absence_note": "임시주주총회라 재무제표 승인 안건이 없습니다."}
    # 여기까지 왔으면 근거가 없다 — 「없다」고 단정하지 않는다.
    return {"absence_kind": "unverified",
            "absence_note": "이 소집공고에서 재무제표를 확인하지 못했습니다 — 원문을 직접 확인하세요."}


def _empty_financial_result() -> dict:
    return {
        "consolidated": {"balance_sheet": None, "income_statement": None},
        "separate": {"balance_sheet": None, "income_statement": None},
    }


def _infer_statement_type(table_el) -> str | None:
    """데이터 테이블 내용을 보고 재무상태표/손익계산서 추론.

    첫 5행의 과목명으로 판별:
    - 자산 + (유동자산 or 비유동자산) → balance_sheet
    - 매출 or 영업이익 → income_statement
    """
    rows = table_el.find_all('tr')
    keywords = []
    for row in rows[:6]:
        cells = row.find_all(['td', 'th'])
        for c in cells:
            keywords.append(re.sub(r'\s+', '', c.get_text()))
    text = ''.join(keywords)
    is_balance_indicators = sum(1 for kw in ['자산', '유동자산', '비유동자산', '부채', '자본총계'] if kw in text)
    # 적자 회사는 「영업손실」·「당기순손실」이라 쓴다 — 이익형만 세면 손실 낸 회사의 손익계산서를
    # 손익계산서로 못 알아본다.
    is_income_indicators = sum(1 for kw in ['매출', '영업이익', '영업손실', '당기순이익',
                                            '당기순손실', '판매비', '관리비'] if kw in text)
    if is_income_indicators >= 2:
        return 'income_statement'
    if is_balance_indicators >= 2:
        return 'balance_sheet'
    return None


def _extract_unit_from_siblings(table_el) -> str:
    """테이블 직전에서 단위 추출 (이전 형제 텍스트 ~600자)."""
    parts = []
    for sib in table_el.previous_siblings:
        t = sib.get_text(strip=True) if hasattr(sib, 'get_text') else str(sib).strip()
        if t:
            parts.append(t[-300:])
        if sum(len(p) for p in parts) > 600:
            break
    prev_text = "".join(reversed(parts))
    m = _FS_UNIT.search(prev_text)
    if m:
        return m.group(1).strip()
    return ""


def _build_column_meta(header_cells: list[str]) -> list[str]:
    """헤더 셀로부터 컬럼 의미 추론 (sub-column 패턴 포함).

    삼성전자 [account, current, current_sub, prior, prior_sub] 같은 5컬럼 패턴 cover.
    """
    columns = []
    for cell in header_cells:
        clean = re.sub(r'\s+', '', cell)
        if ('과' in clean and '목' in clean) or ('구' in clean and '분' in clean):
            columns.append("account")
        elif '주석' in clean:
            columns.append("note")
        elif '당' in clean:
            columns.append("current")
        elif '전' in clean:
            columns.append("prior")
        elif re.match(r'제?\d+기', clean):
            columns.append("_period_by_num")
        elif not clean:
            # 빈 셀 — colspan 확장분, 앞 컬럼의 서브컬럼
            # _period_by_num 다음 빈 셀도 sub-column 처리 (현대차 등 6컬럼 패턴 대응)
            if columns and columns[-1] in ("current", "prior"):
                columns.append(f"{columns[-1]}_sub")
            elif columns and columns[-1] == "_period_by_num":
                columns.append("_period_by_num_sub")
            elif columns and columns[-1] in ("current_sub", "prior_sub", "_period_by_num_sub"):
                # 연속 빈 셀 — 동일 sub-column suffix 유지
                columns.append(columns[-1])
            else:
                columns.append("unknown")
        else:
            columns.append("unknown")

    # _period_by_num → current/prior 변환 (기수 번호 큰 게 당기)
    period_indices = [i for i, c in enumerate(columns) if c == "_period_by_num"]
    if len(period_indices) >= 2:
        nums = []
        for idx in period_indices:
            m = re.search(r'(\d+)', re.sub(r'\s+', '', header_cells[idx]))
            nums.append(int(m.group(1)) if m else 0)
        if nums[0] >= nums[1]:
            columns[period_indices[0]] = "current"
            columns[period_indices[1]] = "prior"
        else:
            columns[period_indices[0]] = "prior"
            columns[period_indices[1]] = "current"
    elif len(period_indices) == 1:
        columns[period_indices[0]] = "current"

    # _period_by_num_sub → 직전 _period_by_num의 매핑에 따라 current_sub / prior_sub
    for i, c in enumerate(columns):
        if c == "_period_by_num_sub":
            # 직전 non-sub label 찾기
            for j in range(i - 1, -1, -1):
                if columns[j] in ("current", "prior"):
                    columns[i] = f"{columns[j]}_sub"
                    break
            else:
                columns[i] = "unknown"

    return columns


def _normalize_financial_rows(columns: list[str], rows: list[list[str]]) -> list[list[str]]:
    """컬럼 패턴 통일 — [account, note, current, prior] 4컬럼.

    sub-column 처리: current_sub/prior_sub 비어있지 않은 첫 값 사용.
    """
    if not columns or not rows:
        return rows

    if columns == ["account", "note", "current", "prior"]:
        return rows

    account_idx = None
    note_idx = None
    current_idxs = []
    prior_idxs = []

    for i, col in enumerate(columns):
        if col == "account" and account_idx is None:
            account_idx = i
        elif col == "note":
            note_idx = i
        elif col in ("current", "current_sub"):
            current_idxs.append(i)
        elif col in ("prior", "prior_sub"):
            prior_idxs.append(i)

    if account_idx is None:
        return rows

    normalized = []
    for row in rows:
        account = row[account_idx] if account_idx < len(row) else ""
        note = row[note_idx] if note_idx is not None and note_idx < len(row) else ""

        current = ""
        for idx in current_idxs:
            if idx < len(row) and row[idx].strip():
                current = row[idx]
                break

        prior = ""
        for idx in prior_idxs:
            if idx < len(row) and row[idx].strip():
                prior = row[idx]
                break

        normalized.append([account, note, current, prior])

    return normalized


def _extract_period_labels(header_cells: list[str]) -> dict:
    """헤더에서 당기/전기 라벨 추출."""
    labels = {"current": "", "prior": ""}
    period_candidates = []
    for h in header_cells:
        h_clean = re.sub(r'\s+', '', h)
        if h_clean in ('당기', '당기말'):
            labels["current"] = h_clean
        elif h_clean in ('전기', '전기말'):
            labels["prior"] = h_clean
        m = re.match(r'제\s*(\d+)\s*\(?\s*(당|전)\s*\)?\s*기', h_clean)
        if m:
            num = int(m.group(1))
            kind = m.group(2)
            if kind == '당':
                labels["current"] = f"제{num}기"
            elif kind == '전':
                labels["prior"] = f"제{num}기"
            period_candidates.append((num, f"제{num}기"))
        else:
            m2 = re.match(r'(?:20)?(\d{2,4})\s*년', h_clean)
            if m2:
                num = int(m2.group(1))
                period_candidates.append((num, h_clean))
    if not labels["current"] and not labels["prior"] and len(period_candidates) >= 2:
        period_candidates.sort(key=lambda x: x[0], reverse=True)
        labels["current"] = period_candidates[0][1]
        labels["prior"] = period_candidates[1][1]
    return labels


# ── 정량 metric 추출 (action tool facts evidence용) ──

_METRIC_KEYWORDS = {
    # 분리 보고 (현대차 등): IS 요약 라인 비어있고 sub-row에만 값.
    # "지배기업소유주지분" 매칭으로 controlling-interest net income 추출.
    # **적자 회사는 「당기순손실」이라 쓴다.** 「당기순이익」만 두면 손실 낸 회사를 통째로 놓친다 —
    # 영풍 2026 소집공고 실측: 본문에 「당기순손실」 9회, 「당기순이익」 1회. 그래서 손익계산서
    # 행을 못 잡고 재무상태표의 「지배기업 소유주지분」(자본)이 대신 걸려 순이익이 3조 6,027억으로
    # 들어갔다(실제 당기순손실 366억).
    # **「당기손익」을 넣으면 안 된다.** 접두 매칭이라 K-IFRS 1109호의 금융상품 분류 명칭
    # 「당기손익-공정가치측정 금융자산/금융상품」과 1001호의 기타포괄손익 구분 표시
    # 「당기손익으로 재분류되지 않는 항목」을 전부 순이익으로 집는다. 둘 다 순이익이 아니다.
    # 금융사는 이 계정이 당기순이익 행보다 **위**에 와서 먼저 매칭된다 — 값의 자릿수가 그럴듯해
    # 사람도 못 잡는다. 표준 표시는 「당기순손익」이므로 「당기손익」은 필요도 없다.
    "net_income_krw": (
        "당기순이익(손실)", "당기순손실(이익)", "당기순이익", "당기순손실",
        "당기 순이익", "당기 순손실", "당기순손익", "연결당기순이익", "연결당기순손실",
        "지배기업소유주지분", "지배기업 소유주지분", "지배기업의 소유주지분", "지배지분 순이익",
    ),
    # 「Ⅰ. 매출」(액 없음) 474건 · 「매출액」1001건 — 둘 다 받는다.
    # 「기타매출」·「기타수익」·「기타영업수익」은 접두 매칭이 걸러낸다.
    # 「Ⅰ. 매출」(액 없음) 474건 · 「매출액」1001건 — 둘 다 받는다.
    # 「기타매출」·「기타수익」·「기타영업수익」은 접두 매칭이 걸러낸다(638건).
    # **보험·금융업은 「보험영업수익」이 매출에 해당**한다 — 접두 매칭이라 명시해야 잡힌다
    # (260729 회귀 검증: 흥국화재·코리안리가 소실됐다. 삼성생명의 「기타영업수익」은 정상 배제).
    "revenue_krw": ("매출액", "매출", "수익(매출액)", "영업수익", "수익 (매출액)",
                    "보험영업수익", "영업수익(매출액)"),
    # 같은 이유로 「영업손실」도 받는다 — 영풍은 「Ⅳ.영업손실」이라 영업이익이 아예 추출되지 않았다.
    "operating_profit_krw": ("영업이익(손실)", "영업손실(이익)", "영업이익", "영업손실", "영업손익"),
    "total_assets_krw": ("자산총계", "자산 총계"),
    "total_liabilities_krw": ("부채총계", "부채 총계"),
    "total_equity_krw": ("자본총계", "자본 총계"),
}

# 잠정 재무제표에 잘못 끼는 비-FS 테이블 거부 패턴 (셀트리온 등).
# account 컬럼 raw text에 영문 사명 라인 다수 (≥6) 있으면 종속회사 목록으로 판단 → reject.
_NON_FS_TABLE_HINTS = ("Inc.", "Ltd.", "Pte.", "B.V.", "S.A.S.", "K.K.", "Co.,Ltd",
                       "Limited", "Corporation", "PTE.", "LTD")


# 접두 매칭이라도 「매출원가」·「매출총이익」은 「매출」로 시작한다 — 명시적으로 막는다.
# (260729 테스트가 잡음: 「매출」 키워드를 넣자마자 원가·총이익이 매출로 들어왔다)
_REVENUE_EXCLUDE = ("매출원가", "매출총이익", "매출채권", "매출할인", "매출에누리",
                    "영업수익원가", "보험영업비용")


#: **같은 계정명이 두 표에서 다른 것을 뜻한다.** 「지배기업 소유주지분」은 손익계산서에서는
#: 당기순이익 귀속(현대차식 분리 보고)이지만, 재무상태표 자본 섹션에서는 **지배주주 귀속 자본**이다.
#: 영풍 실측 — 재무상태표 「I. 지배기업 소유주지분 3,602,707,444,005」가 순이익으로 들어갔다.
#: 표 종류를 이미 알고 있으니 손익계산서에서만 인정한다.
#: 순이익 값이 실제로 **순이익 계정**에서 왔는지 마지막에 확인한다. 항목번호를 뗀 계정명이
#: 「(연결)당기순이익/손실/손익」이거나 귀속 표시(「지배기업 소유주지분」·「지배지분 순이익」)여야
#: 한다. 「당기손익-공정가치…」·「기타포괄손익…」은 여기서 걸린다.
_NET_INCOME_ACCOUNT_OK = re.compile(
    r"^(연결)?당기순(이익|손실|손익)|^지배(기업(의)?소유주지분|지분순이익)"
)
_INCOME_STATEMENT_ONLY = (
    "지배기업소유주지분", "지배기업의소유주지분", "지배지분순이익", "비지배지분",
)


def _account_matches(account_clean: str, keywords, metric_key: str,
                     stmt_type: str | None = None) -> bool:
    if metric_key == "revenue_krw" and account_clean.startswith(_REVENUE_EXCLUDE):
        return False
    for kw in keywords:
        kw_clean = kw.replace(" ", "")
        if not account_clean.startswith(kw_clean):
            continue
        if kw_clean in _INCOME_STATEMENT_ONLY and stmt_type != "income_statement":
            continue
        return True
    return False


def _strip_item_marker(s: str) -> str:
    """계정명 앞의 항목 번호를 뗀다 — 「Ⅰ.매출」·「1.매출액」·「(1)매출」."""
    return re.sub(r"^[\(（]?[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩIVXivx0-9]{1,4}[\)）]?\s*[.．:：]?\s*", "", s)


def _parse_amount(text: str) -> int | None:
    """숫자 문자열 → int (콤마 / 괄호 음수)."""
    if not text:
        return None
    s = text.strip().replace(",", "").replace(" ", "")
    if not s or s in ("-", "—"):
        return None
    is_negative = False
    if s.startswith("(") and s.endswith(")"):
        is_negative = True
        s = s[1:-1]
    if s.startswith("-"):
        is_negative = True
        s = s[1:]
    try:
        v = int(float(s))
    except (ValueError, TypeError):
        return None
    return -v if is_negative else v


#: 외화 표시 재무제표. 원 환산에는 환율과 기준일이 필요한데 본문에 그 정보가 없다.
#: 실측 코오롱티슈진(「USD」)·두산밥캣(「USD천」) — 예전에는 계수 1이 나가 **USD 숫자가 그대로
#: 원화 필드에 들어갔다**(티슈진 당기순손실 1억 vs 실제 135,413,281 USD ≈ 1,880억원).
#: 국내 상장 외국법인은 본국 통화로 보고한다 — 미국계(950xxx)는 USD, 중국계(900xxx)는 RMB/CNY/元.
#: 통화 코드가 빠지면 그 회사만 조용히 원화로 읽힌다(실측: 「CNY」만 넣었더니 「RMB」를 쓰는
#: 컬러레이가 새어나갔다). **목록만으로는 계속 샌다** — 아래 `_KRW_UNIT` 화이트리스트가 보루다.
_FOREIGN_UNIT = re.compile(
    # ISO 4217 — 국내 상장사·해외법인이 실제로 쓰는 범위
    r"USD|JPY|EUR|CNY|RMB|GBP|HKD|SGD|AUD|NZD|CAD|CHF|SEK|NOK|DKK"
    r"|VND|TWD|THB|IDR|INR|MYR|PHP|MMK|BDT|PKR|LKR|KHR|LAK|BND|MOP"
    r"|RUB|KZT|UZS|TRY|PLN|CZK|HUF|BRL|MXN|CLP|COP|ARS|PEN"
    r"|ZAR|EGP|NGN|AED|SAR|QAR|KWD|ILS"
    # 한글·한자·기호 표기. 한 글자(「엔」·「동」)는 오탐이 커서 넣지 않는다.
    r"|미화|외화|외국통화|달러|달라|엔화|일본엔|유로화?|위안화?|파운드|루피|루블|바트|링깃|페소"
    r"|元|美元|日元|港[币幣]|人民[币幣]"
    r"|\$|￥|¥|€|£|₫|₹|₽|₱|฿"
)
#: **원화 표기는 반드시 「원」을 포함한다** — 캐시 소집공고 실측 전수(원 458·백만원 189·천원 92·
#: 억원 4·「주, 천원」·「백만원, 주당순이익 : 원」). 「원」이 없는 단위는 우리가 모르는 통화일 수
#: 있으므로 원화로 단정하지 않는다. 통화 목록에 빠진 것이 나와도 여기서 막힌다.
_KRW_UNIT = re.compile(r"원")


def _scale_factor(unit: str | None) -> int | None:
    """unit 문자열 → 원 환산 계수. **외화면 `None`** — 환산할 수 없으면 값을 내지 않는다.

    조용히 1을 돌려주면 통화가 다른 숫자가 원화 필드에 들어가고, 자릿수가 그럴듯해
    검산도 통과한다(외화끼리는 자산=부채+자본이 맞고 순이익<매출도 성립). 틀린 값이 빈 값보다 나쁘다.
    """
    if not unit:
        return 1          # 표기가 없으면 DART 기본인 원
    u = unit.replace(" ", "")
    if _FOREIGN_UNIT.search(u):
        return None
    if not _KRW_UNIT.search(u):
        # 「원」이 없다 — 우리가 모르는 통화일 수 있다. 원화로 단정하지 않는다.
        return None
    # 「십억원」이 「억원」에 걸리면 1/10 로 환산된다 — 더 긴 것부터 본다.
    if "십억원" in u:
        return 1_000_000_000
    if "백만원" in u:
        return 1_000_000
    if "천원" in u:
        return 1_000
    if "억원" in u:
        return 100_000_000
    return 1


def extract_metrics(parsed: dict[str, Any], prefer: str = "consolidated") -> dict[str, Any]:
    """parse_provisional_financial_statement 결과 → 정량 metric flat dict.

    proxy_advise facts evidence용. 우선 연결, 없으면 별도.

    return:
        {
          "fy_current_net_income_krw": int | None,
          "fy_prior_net_income_krw": int | None,
          ...
          "extraction_status": "success" | "partial" | "no_data",
          "scope_used": "consolidated" | "separate" | None,
        }
    """
    out: dict[str, Any] = {"extraction_status": "no_data", "scope_used": None}

    scope_order = (prefer, "separate" if prefer == "consolidated" else "consolidated")
    last_extraction_scope: str | None = None
    for scope in scope_order:
        scope_data = parsed.get(scope, {}) or {}
        if not scope_data:
            continue
        income = scope_data.get("income_statement")
        balance = scope_data.get("balance_sheet")
        if not income and not balance:
            continue

        n_extracted = 0

        for table, stmt_type in ((income, "income_statement"), (balance, "balance_sheet")):
            if not table or not table.get("rows"):
                continue
            # 종속회사 목록 등 비-FS 테이블 거부 (account 영문 사명 ≥6 줄)
            account_lines = [(r[0] if r else "") for r in table.get("rows", [])]
            non_fs_hint_count = sum(
                1 for a in account_lines
                if any(hint in a for hint in _NON_FS_TABLE_HINTS)
            )
            if non_fs_hint_count >= 6:
                continue

            unit = table.get("unit") or ""
            scale = _scale_factor(unit)
            if scale is None:
                # 외화 표시 — 환산 근거가 본문에 없다. 값을 내지 않고 그 사실을 남긴다.
                out.setdefault("skipped_units", []).append(unit)
                continue
            cols = table.get("columns") or []
            try:
                acc_idx = cols.index("account")
                cur_idx = cols.index("current")
                prior_idx = cols.index("prior")
            except ValueError:
                continue

            for row in table["rows"]:
                if len(row) <= max(acc_idx, cur_idx, prior_idx):
                    continue
                account = (row[acc_idx] or "").strip()
                if not account:
                    continue
                # 「Ⅰ.」·「1.」 같은 항목 번호를 떼고 본다 — 원문은 「Ⅰ. 매출」처럼 쓴다.
                account_clean = _strip_item_marker(account.replace(" ", ""))

                for metric_key, keywords in _METRIC_KEYWORDS.items():
                    cur_key = f"fy_current_{metric_key}"
                    prior_key = f"fy_prior_{metric_key}"
                    if cur_key in out:
                        continue
                    # **접두** 매칭 — 부분 포함이면 「기타영업수익」이 「영업수익」에 걸린다.
                    # 260729 실측: LG화학 매출이 45.9조 대신 기타영업수익 1.65조로 들어갔다.
                    # 캐시 소집공고 479건에 「기타수익」541·「기타매출」97·「기타영업수익」10건.
                    if _account_matches(account_clean, keywords, metric_key, stmt_type):
                        cur_val = _parse_amount(row[cur_idx])
                        prior_val = _parse_amount(row[prior_idx])
                        if cur_val is not None:
                            out[cur_key] = cur_val * scale
                            # **어느 계정에서 뽑았는지** 남긴다. 값만 있으면 틀렸을 때 어디서
                            # 왔는지 알 수 없다 — 영풍은 순이익이 재무상태표 「지배기업 소유주지분」
                            # (자본)에서 왔는데, 3조 6,027억이라는 숫자만 보고는 알 방법이 없었다.
                            # 표를 통째로 싣는 것보다 작고 정확하다.
                            out.setdefault("source_accounts", {})[metric_key] = {
                                "account": account, "statement": stmt_type, "scope": scope,
                            }
                            n_extracted += 1
                            last_extraction_scope = scope
                        if prior_val is not None:
                            out[prior_key] = prior_val * scale

        # **출처 게이트** — 순이익은 「순이익 계정에서 왔는가」로 한 번 더 거른다.
        # 키워드 사전은 새 계정명이 나올 때마다 구멍이 생기고, 그 구멍으로 엉뚱한 행이 들어온다
        # (영풍=재무상태표 자본, 금융사=FVPL 금융상품). **틀린 값이 빈 값보다 훨씬 나쁘므로**
        # 순이익 계열이 아니면 버린다 — 값이 없으면 검산·판정이 「확인 못 했다」로 정직하게 간다.
        # **매칭과 같은 형태로 본다** — 매칭은 `_strip_item_marker` 로 「Ⅶ.」·「XI. 」를 떼고 보는데
        # 게이트만 원본을 보면 「Ⅶ.당기순손실」이 통과했다가 여기서 삭제된다. 실측 48사 중 24건
        # (영풍·POSCO홀딩스·HD현대·삼성물산·롯데케미칼 등)의 **정당한 순이익이 사라졌다**.
        _src = (out.get("source_accounts") or {}).get("net_income_krw")
        _acc = _strip_item_marker((_src or {}).get("account", "").replace(" ", "")) if _src else ""
        if _src and not _NET_INCOME_ACCOUNT_OK.match(_acc):
            for k in ("fy_current_net_income_krw", "fy_prior_net_income_krw"):
                out.pop(k, None)
            out.setdefault("rejected_accounts", {})["net_income_krw"] = _src.get("account")
            out["source_accounts"].pop("net_income_krw", None)

        # scope_used: 실제로 metric을 추출한 마지막 scope (현재 pass 또는 이전 pass)
        if last_extraction_scope:
            out["scope_used"] = last_extraction_scope

        if n_extracted >= 3:
            out["extraction_status"] = "success"
            return out
        elif n_extracted > 0:
            out["extraction_status"] = "partial"

    return out
