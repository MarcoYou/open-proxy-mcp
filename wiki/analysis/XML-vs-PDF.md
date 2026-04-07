---
type: analysis
title: XML vs PDF 비교 분석
tags: [parser, architecture, comparison]
sources: [DEVLOG.md]
related: [3-tier-fallback, opendataloader]
---

# XML vs PDF 비교 분석

## 결론

**XML 1차 + PDF 보강이 최적 전략.** PDF-only 전환은 financials/agenda에서 역효과.

## 비교 데이터 (KOSPI 200, 198개)

### XML이 우세한 영역
- **financials**: XML의 HTML 테이블 구조가 bs4로 깔끔하게 파싱됨. PDF는 opendataloader 변환 시 테이블 구조 손실 위험.
- **agenda**: XML의 섹션 태그(`<section-1>`)가 정확한 경계 제공. PDF는 텍스트 기반이라 경계 판별 어려움.

### PDF가 우세한 영역
- **personnel 경력**: XML에서 병합된 경력이 PDF에서는 개별 줄로 분리됨 (미래에셋증권: 245자 1줄 -> 17건)
- **compensation**: XML에서 비표준 구조인 기업(기업은행 등)도 PDF에서 정상 파싱

### 파서별 PDF 성능 추이 (v1 -> 최종)

| 파서 | v1 | 최종 | 개선폭 |
|------|-----|------|--------|
| compensation | 88.9% | 97.5% | +8.6% |
| personnel | 89.9% | 93.9% | +4.0% |
| financials BS | 82.3% | 96.0% | +13.7% |
| financials IS | 12.6% | 93.9% | +81.3% |
| aoi | 76.3% | 97.0% | +20.7% |
| agenda | 80.3% | 97.5% | +17.2% |

## 아키텍처 결정

XML을 기본으로 사용하되, XML SOFT_FAIL/HARD_FAIL 시 PDF로 보강. PDF-only로 전환하면 XML이 잘 되는 영역에서 오히려 성능 하락.
