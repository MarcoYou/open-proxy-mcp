"""오늘 — 한국 시각(KST) 기준.

DART 접수일(`rcept_dt`)은 한국 달력이다. 서버(fly)는 UTC 로 돌아서 `date.today()` 가
KST 자정~09시 사이에는 **어제**를 준다. 그 시간대에 「오늘까지」로 만든 조회 구간(`end_de`)과
기준일(as_of)은 오늘 접수된 공시를 잘라낸다 — 아침에 뜬 공시를 사용자가 말해 줘야 알던 원인 중
하나다(260904). 날짜가 「오늘」을 뜻하는 자리는 전부 이 함수를 쓴다.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9), name="KST")


def today_kst() -> date:
    return datetime.now(KST).date()
