#!/usr/bin/env bash
# 재무 SSOT 갱신 체인 (go-forward) — 신규 공시분만 append. 연 4회 공시 마감 후 실행.
#
# 공시 캘린더(마감): 사업보고서 3/31 · 1Q 5/15 · 반기 8/14 · 3Q 11/14.
#   → 각 마감 며칠 뒤 1회 실행하면 그 분기/연도만 수집(resume·공시-인지로 0낭비).
#
# **DART 키 필요**(OPENDART_API_KEY[, _2]). 보안 제약상 키는 fly secrets 보관 → 이 스크립트는
#   fly machine 또는 키 보유 로컬에서 실행(GitHub Actions에 키 복제 금지 — IP차단 가능 키의 노출면
#   확대 회피, API-spec 검토 권고). 0-DART인 derive는 일일 워크플로(market-val-weekly.yml)가 별도
#   수행하므로 여기 fetch만 하면 됨.
#
# 배포(권장 = fly 스케줄 머신, 키 이미 fly secrets에 있음·신규 노출 0):
#   fly machine run . --schedule weekly --restart no \
#     --command "bash scripts/refresh_financials.sh"       # 앱 이미지·secrets 상속
#   매주 1회면 충분(멱등·resume·공시-인지 → 신규 없는 주는 ≈0콜, 공시 직후 1주만 실제 수집).
#   off-peak(새벽 KST) 권장 — fly 웹서버와 IP·키 공유라 동시 배치 금지(cross-process 리미터 사각).
#   ※ 수동 대안: 공시 마감 며칠 뒤(4/5·5/20·8/20·11/20) fly ssh 또는 로컬에서 직접 실행.
#
# rate limit: 각 스크립트가 동시성 1~2 + sleep + client throttle(910/분)로 하드룰 준수.
# 신규 없으면(공시 전/이미 수집) 각 단계 즉시 종료 — 매주 돌려도 무해(멱등·resume).
#
# 실행: bash scripts/refresh_financials.sh
set -euo pipefail
cd "$(dirname "$0")/.."

echo "═══ [1/4] 연간 재무(mkt_finstat_y) 신규 FY — market_val_series --fetch ═══"
python3 scripts/market_val_series.py --fetch

echo "═══ [2/4] Q4 seed 재생성(신규 연간 반영) — market_fund_quarterly --seed ═══"
python3 scripts/market_fund_quarterly.py --seed

echo "═══ [3/4] 분기 재무(mkt_finstat_q) 신규 분기 — market_fund_quarterly --fetch ═══"
python3 scripts/market_fund_quarterly.py --fetch

echo "═══ [4/4] mkt_fundamentals 파생 갱신(0-DART) — market_fund_quarterly --derive ═══"
python3 scripts/market_fund_quarterly.py --derive

echo "✓ 재무 SSOT 갱신 완료. 일일 market-val-weekly가 스냅샷(firm_valuation_snapshot·mkt_val_history[시장·섹터])에 반영."
