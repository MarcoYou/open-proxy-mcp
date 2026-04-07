---
type: analysis
title: BeautifulSoup 파서 선택
tags: [parser, benchmark, lxml, beautifulsoup]
sources: [DEVLOG.md]
related: [OpenProxy-MCP, XML-vs-PDF]
---

# BeautifulSoup 파서 선택

## 결론

**lxml 채택.** html.parser 대비 30% 빠르고 결과 동일. html5lib은 79% 느림.

## 벤치마크 (250건 전수)

| 파서 | zone 성공 | 속도 | 결과 차이 |
|------|-----------|------|----------|
| html.parser | 246/250 | 89ms/doc | baseline |
| **lxml** | **246/250** | **62ms/doc** | **0건** |
| html5lib | 246/250 | 159ms/doc | 0건 |

## regex 라이브러리 평가

- `regex` 모듈: `re` 대비 60% 느림
- `\p{Hangul}`: DART에서 불필요 (자모 443자 추가 매치하나 미사용)
- `[가-하]` 범위: 오히려 `갸`, `거` 등 오매치
- **결론: 도입하지 않음**, 명시적 나열이 안전

## lxml-xml 파서

DART 문서의 대소문자 혼용으로 사용 불가. HTML 모드의 lxml이 더 관대.
