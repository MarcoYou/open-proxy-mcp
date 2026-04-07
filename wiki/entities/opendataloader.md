---
type: entity
title: opendataloader
tags: [library, pdf, parser]
related: [3-tier-fallback, Upstage-OCR]
---

# opendataloader

## 개요

PDF를 마크다운으로 변환하는 라이브러리. [[3-tier-fallback]]의 2번째 tier(_pdf)에서 사용.

## OPM에서의 활용

- DART에서 다운로드한 PDF를 마크다운 테이블로 변환
- 최적 설정: `table_method="cluster"` + `keep_line_breaks=True`
- pdf_parser.py에서 호출

## 성능

- KOSPI 200 전체 198개 PDF 다운로드 + 파싱 완료
- XML 실패 케이스에서 PDF가 유효 데이터 추출 성공 (예: 기업은행, 미래에셋증권)

## 한계

- 일부 PDF에서 변환 품질 불안정 (opendataloader 자체 한계)
- 이런 경우 [[Upstage-OCR]]로 최종 fallback
