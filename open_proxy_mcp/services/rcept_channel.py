"""접수번호(rcept_no)로 접수 창구를 읽는다.

DART 접수번호는 14자리다 — 앞 8자리 + 일련번호 6자리. **일련번호 첫 자리(전체 9번째 자리)가
접수 창구**다.

| 9번째 자리 | 창구 |
|---|---|
| 0 | DART 전자공시(회사·보고자가 DART 에 직접 제출 — 정기보고서·주요사항보고서·소집공고·지분공시 등) |
| 1 | 금융감독원 안내(효력발생 안내 등) |
| 6 | 거래소 코넥스시장 수시공시 |
| 7 | 거래소 채권시장 공시(채권 상장사, 드물다) |
| 8 | 거래소 유가증권시장(코스피) 수시공시 |
| 9 | 거래소 코스닥시장 수시공시 |

- **목록을 닫지 않는다.** 2·3·4·5 는 지금까지 표본에 없었을 뿐 없다는 뜻이 아니다. 모르는 자리는
  오류가 아니라 `unknown` 이다.
- 거래소 계열도 DART 가 발번하고 DART 에 전재된다 — `document.xml` 로 똑같이 열린다.
  계열은 「누가 받았나」이지 「어디서 읽나」가 아니다.
- 앞 8자리는 **발번일**이다. 대개 접수일과 같지만 다를 때가 있다(전체의 2% 안팎). 접수일이 필요하면
  목록의 `rcept_dt` 를 쓴다.
- 10번째 자리는 지금까지 늘 `0` 이었다. 판정에 쓰지 않는다.
- 거래소(KIND) 자체 번호는 DART 접수번호와 **다른 번호 체계**다. 유가증권시장(8계열)만
  「9~10번째 자리 `80` → `00`」 변환이 표본으로 확인돼 있다
  (`wiki/raw/references/dart-kind-mapping-whitelist-2026-04.json`). 코스닥·코넥스 계열의 변환은
  확인하지 않았으므로 만들지 않는다.
"""

from __future__ import annotations

#: 9번째 자리 → 창구 이름. 닫힌 목록이 아니다 — 없는 자리는 `unknown`.
RCEPT_CHANNELS: dict[str, str] = {
    "0": "dart",
    "1": "fss",
    "6": "krx_konex",
    "7": "krx_bond",
    "8": "krx_kospi",
    "9": "krx_kosdaq",
}

#: 거래소 수시공시 계열.
EXCHANGE_CHANNELS = frozenset({"krx_konex", "krx_bond", "krx_kospi", "krx_kosdaq"})


def is_rcept_no(rcept_no: str) -> bool:
    """14자리 숫자인가."""
    return bool(rcept_no) and len(rcept_no) == 14 and rcept_no.isdigit()


def rcept_channel(rcept_no: str) -> str:
    """접수 창구 이름. 14자리 숫자가 아니거나 모르는 자리면 `unknown`."""
    if not is_rcept_no(rcept_no or ""):
        return "unknown"
    return RCEPT_CHANNELS.get(rcept_no[8], "unknown")


def is_exchange_filing(rcept_no: str) -> bool:
    """거래소(유가·코스닥·코넥스·채권) 수시공시 계열인가."""
    return rcept_channel(rcept_no) in EXCHANGE_CHANNELS


def kind_acptno_for(rcept_no: str) -> str | None:
    """거래소 KIND 접수번호로 바꾼다. 확인된 변환(유가증권시장 8계열)만 — 나머지는 None."""
    if rcept_channel(rcept_no) != "krx_kospi" or rcept_no[9] != "0":
        return None
    return rcept_no[:8] + "00" + rcept_no[10:]
