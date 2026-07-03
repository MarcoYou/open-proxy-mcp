"""Supabase DB 용량 리포트 — 어떤 테이블이 얼마나 차지하는지 + 무료티어(500MB) 경고.

사용: python3 scripts/db_usage.py   (조치는 안 함 — 보고/워닝만)
"""
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
import psycopg

FREE_TIER_MB = 500
WARN_PCT = 70

def main():
    con = psycopg.connect(os.environ["DATABASE_URL"])
    total_b = con.execute("SELECT pg_database_size(current_database())").fetchone()[0]
    total_mb = total_b / 1024 / 1024
    pct = total_mb / FREE_TIER_MB * 100
    bar = "█" * round(pct / 100 * 20) + "░" * (20 - round(pct / 100 * 20))
    print(f"[{bar}] {total_mb:,.0f} MB / {FREE_TIER_MB} MB (무료티어 {pct:.0f}%)")
    if pct >= WARN_PCT:
        print(f"⚠️  경고: 무료티어 {WARN_PCT}% 초과 — events drain 또는 플랜 업그레이드 검토")
    print()
    rows = con.execute("""
      SELECT relname, pg_total_relation_size(relid) b, n_live_tup
      FROM pg_stat_user_tables JOIN pg_statio_user_tables USING(relid, relname)
      WHERE pg_total_relation_size(relid) > 100*1024
      ORDER BY 2 DESC LIMIT 12""").fetchall()
    for name, b, n in rows:
        share = b / total_b * 100
        print(f"  {name:<24} {b/1024/1024:>7.1f} MB  ({share:>4.1f}%)  {n:>10,}행")
    con.close()

if __name__ == "__main__":
    main()
