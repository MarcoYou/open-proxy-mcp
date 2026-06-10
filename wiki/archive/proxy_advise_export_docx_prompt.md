---
type: architecture
title: proxy_advise_export_docx 실행 프롬프트
created: 2026-05-13
updated: 2026-05-13
related: [proxy_advise_word_report_spec, proxy_advise_word_report_design, proxy_advise_before_meeting]
---

# proxy_advise_export_docx 실행 프롬프트

이 문서는 사용자가 Claude web 또는 유사한 MCP 클라이언트에서 `proxy_advise` 결과를 **균일한 `.docx` 문서**로 뽑고 싶을 때 그대로 재사용할 수 있는 단일 프롬프트다.

핵심 원칙:

- 사용자는 로컬 디렉토리나 repo 구조를 알 필요가 없다.
- 샘플 문서 참조는 사용자 경로가 아니라 **서버가 내부적으로 보유한 sample corpus**를 기준으로 한다.
- 결과는 자유서술 markdown이 아니라 **고정 Word 양식**으로 수렴해야 한다.

## 사용 목적

아래와 같은 요청을 안정적으로 `proxy_advise_export_docx` 실행으로 연결한다.

- “LG화학 주총 의안 자문 보고서 워드로 뽑아줘”
- “이번 proxy advise 결과 문서화해서 docx로 만들어줘”
- “주주제안 포함해서 의결권 행사 검토 메모 문서로 정리해줘”

## 실행 프롬프트

```text
Use the built-in proxy_advise Word-report specification and canonical design as the governing contract for this export. Generate a .docx report through `proxy_advise_export_docx`, not a free-form markdown answer.

Requirements:
- Use the built-in sample advisor-report corpus only as a reference for common report structure, not as a template to copy.
- Use the current OPM proxy_advise data model and available evidence as the hard constraint.
- Produce one standard canonical Word report format, not multiple style families.
- Keep the output implementation-oriented and uniform across runs.
- Preserve clear sections for summary, meeting metadata, agenda recommendation table, agenda-by-agenda reasoning, candidate appendix, evidence appendix, and caveats.
- Apply the fixed visual design rules defined by the built-in Word-report design document, including color, typography, table layout, spacing, and status highlighting.
- Do not invent fields that are not present in OPM data; mark them as missing or derived if needed.
- Prefer reusing an existing report context or proxy_advise result if available; avoid unnecessary re-execution.

Expected tool:
- `proxy_advise_export_docx`

Expected output:
- generated `.docx` artifact
- concise preview summary
- metadata about report scope and evidence coverage
```

## 구현 해석 규칙

이 프롬프트를 해석하는 쪽은 아래를 따른다.

1. 샘플 참조는 내부 corpus에서 수행한다.
2. 최종 산출은 `proxy_advise_export_docx`가 담당한다.
3. 문서 양식은 단일 canonical sample 기준으로 고정한다.
4. 사용자가 따로 요청하지 않는 한 `compact`, `full` 같은 변형 스타일로 분기하지 않는다.

## 관련 기준 문서

- [[proxy_advise_word_report_spec]]
- [[proxy_advise_word_report_design]]
- [[proxy_advise_before_meeting]]
