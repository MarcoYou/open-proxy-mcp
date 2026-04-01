"""MCP tool 공통 에러 응답 헬퍼"""


def tool_error(action: str, error: Exception = None, ticker: str = "", rcept_no: str = "") -> str:
    """통일된 에러 응답"""
    context = []
    if ticker:
        context.append(f"ticker={ticker}")
    if rcept_no:
        context.append(f"rcept_no={rcept_no}")
    ctx = f" ({', '.join(context)})" if context else ""
    err_msg = f": {error}" if error else ""
    return f"[오류] {action} 실패{err_msg}{ctx}"


def tool_not_found(entity: str, query: str) -> str:
    """엔터티를 찾을 수 없음"""
    return f"'{query}'에 해당하는 {entity}을(를) 찾을 수 없습니다."


def tool_empty(action: str, fallback: str = "") -> str:
    """데이터 없음"""
    fb = f" {fallback}" if fallback else ""
    return f"{action} 데이터가 없습니다.{fb}"
