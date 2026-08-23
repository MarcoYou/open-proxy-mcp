"""전 상장사 기능통화 스캔 — 두산밥캣류(비KRW 재무) 전수 탐지.

배경(260704): 두산밥캣(USD 기능통화)처럼 DART가 재무를 비KRW로 주는 회사는 KRW 시총과 통화
불일치로 배수가 왜곡된다. valuation.py의 FX 환산은 generic(통화 감지 기반)이라 자동 처리되나,
"몇 개나 있고 다 정상인가"를 전수로 확인. 종목당 1콜(저장된 fs 재사용, FY2024).

결과: dart_fundamentals.currency 컬럼에 저장 + 비KRW 종목 출력.

실행: python3 scripts/scan_currency.py
"""
import asyncio, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
import psycopg
from open_proxy_mcp.dart.fx import statement_currency

DDL_MIGRATE = "ALTER TABLE dart_fundamentals ADD COLUMN IF NOT EXISTS currency text"


def _flush(buf):
    if not buf:
        return
    for attempt in (1, 2):
        try:
            with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=15) as c:
                with c.cursor() as cur:
                    cur.executemany(
                        "UPDATE dart_fundamentals SET currency=%s WHERE ticker=%s", buf)
                c.commit()
            buf.clear(); return
        except psycopg.OperationalError:
            if attempt == 2: raise


async def main():
    from open_proxy_mcp.dart.client import get_dart_client, DartClientError
    con = psycopg.connect(os.environ["DATABASE_URL"])
    con.execute(DDL_MIGRATE); con.commit()
    firms = con.execute(
        "SELECT ticker, corp_code, fs FROM dart_fundamentals "
        "WHERE fetched='ok' AND currency IS NULL ORDER BY ticker").fetchall()
    con.close()
    print(f"대상 {len(firms)}사 (currency 미확인분)", flush=True)
    c = get_dart_client(); buf = []; k = 0; nonkrw = []
    for isu, cc, fs in firms:
        k += 1
        try:
            d = await c.get_fnltt_singl_acnt_all(cc, "2024", "11011", fs or "CFS")
            rows = (d.get("list") or []) if isinstance(d, dict) else []
            await asyncio.sleep(0.45)
            if not rows:  # 저장 fs로 비면 반대 시도
                alt = "OFS" if fs == "CFS" else "CFS"
                d = await c.get_fnltt_singl_acnt_all(cc, "2024", "11011", alt)
                rows = (d.get("list") or []) if isinstance(d, dict) else []
                await asyncio.sleep(0.45)
            cur = statement_currency(rows) if rows else "?"
            buf.append((cur, isu))
            if cur not in ("KRW", "?"):
                nonkrw.append((isu, cur))
                print(f"  ★ 비KRW: {isu} = {cur}", flush=True)
            if len(buf) >= 25: _flush(buf)
        except DartClientError as e:
            if "[013]" in str(e):
                buf.append(("nodata", isu))
            if len(buf) >= 25: _flush(buf)
        except Exception as e:
            en = type(e).__name__
            if "ReadError" in en or "Connect" in en or "Timeout" in en:
                _flush(buf); print(f"네트워크({en}) — 중단(재개 가능)", flush=True); break
        if k % 200 == 0: print(f"{k}/{len(firms)} · 비KRW {len(nonkrw)}", flush=True)
    _flush(buf)
    print(f"\n스캔 종료. 비KRW {len(nonkrw)}사:")
    for isu, cur in nonkrw: print(f"  {isu} {cur}")


if __name__ == "__main__":
    asyncio.run(main())
