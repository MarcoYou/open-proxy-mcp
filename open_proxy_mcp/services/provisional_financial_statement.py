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
    is_income_indicators = sum(1 for kw in ['매출', '영업이익', '당기순이익', '판매비', '관리비'] if kw in text)
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
            if columns and columns[-1] in ("current", "prior"):
                columns.append(f"{columns[-1]}_sub")
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
    "net_income_krw": (
        "당기순이익(손실)", "당기순이익", "당기 순이익", "당기손익",
        "지배기업소유주지분", "지배기업 소유주지분", "지배기업의 소유주지분", "지배지분 순이익",
    ),
    "revenue_krw": ("매출액", "수익(매출액)", "영업수익", "수익 (매출액)"),
    "operating_profit_krw": ("영업이익(손실)", "영업이익", "영업손익"),
    "total_assets_krw": ("자산총계", "자산 총계"),
    "total_liabilities_krw": ("부채총계", "부채 총계"),
    "total_equity_krw": ("자본총계", "자본 총계"),
}

# 잠정 재무제표에 잘못 끼는 비-FS 테이블 거부 패턴 (셀트리온 등).
# account 컬럼 raw text에 영문 사명 라인 다수 (≥6) 있으면 종속회사 목록으로 판단 → reject.
_NON_FS_TABLE_HINTS = ("Inc.", "Ltd.", "Pte.", "B.V.", "S.A.S.", "K.K.", "Co.,Ltd",
                       "Limited", "Corporation", "PTE.", "LTD")


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


def _scale_factor(unit: str | None) -> int:
    """unit 문자열 → krw 환산 계수."""
    if not unit:
        return 1
    u = unit.replace(" ", "")
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

        for table in (income, balance):
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
                account_clean = account.replace(" ", "")

                for metric_key, keywords in _METRIC_KEYWORDS.items():
                    cur_key = f"fy_current_{metric_key}"
                    prior_key = f"fy_prior_{metric_key}"
                    if cur_key in out:
                        continue
                    if any(kw.replace(" ", "") in account_clean for kw in keywords):
                        cur_val = _parse_amount(row[cur_idx])
                        prior_val = _parse_amount(row[prior_idx])
                        if cur_val is not None:
                            out[cur_key] = cur_val * scale
                            n_extracted += 1
                            last_extraction_scope = scope
                        if prior_val is not None:
                            out[prior_key] = prior_val * scale

        # scope_used: 실제로 metric을 추출한 마지막 scope (현재 pass 또는 이전 pass)
        if last_extraction_scope:
            out["scope_used"] = last_extraction_scope

        if n_extracted >= 3:
            out["extraction_status"] = "success"
            return out
        elif n_extracted > 0:
            out["extraction_status"] = "partial"

    return out
