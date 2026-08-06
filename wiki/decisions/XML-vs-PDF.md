---
type: decision
title: XML vs PDF — 왜 XML 단독인가
tags: [parser, architecture, comparison]
sources: [git history, wiki/archive/sources/devlog]
related: [3-tier-fallback]
---

# XML vs PDF — 왜 XML 단독인가

## 결론 (현행)

**OPM 은 `document.xml` 단독으로 파싱한다.** PDF·OCR 폴백은 2026-07-12 OPM 에서 폐기하고 고급
프로덕트 `open-proxy-ai` 로 이관했다. XML 이 불완전하면 원문을 호출측 AI 에 노출해 보정(soft-fail)하고,
조작된 값을 만들어 내지 않는다. 아키텍처는 [[3-tier-fallback]].

## 왜 XML 이 기본인가

- **financials**: XML 의 HTML 테이블 구조가 그대로 살아 있어 표를 격자로 복원할 수 있다. PDF 변환은
  테이블 구조를 잃을 위험이 있다.
- **agenda**: XML 의 섹션 태그(`<section-1>`)가 안건 경계를 **정확히** 준다. PDF 는 텍스트 기반이라
  경계를 추정해야 한다.

즉 XML 은 **문서가 스스로 선언한 구조**를 읽을 수 있고 PDF 는 그것을 잃는다. 이것이 XML 을 기본으로
두는 이유이며, PDF 로 전면 전환하면 XML 이 잘 되던 영역이 오히려 나빠진다.

## PDF 가 낫던 영역 (참고 — 현재 OPM 경로 아님)

`personnel` 경력(XML 에서 한 줄로 병합된 경력이 PDF 에선 줄 단위로 분리)과 비표준 구조의
`compensation` 은 PDF 가 유리했다. 그래서 이 문제들은 PDF 폴백이 아니라 **XML 쪽 파서 보강**으로
해결해야 한다.

> PDF 파서 전수 census(KOSPI 200 198사, 파서별 v1→최종 성능표)와 「XML 1차 + PDF 보강」 시절 전략은
> storage (`wiki-private/architecture/이관_260806_arch-decisions.md`).
