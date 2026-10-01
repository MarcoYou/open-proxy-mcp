"""한/영 릴리즈 제목은 알려진 상태 번역만 허용한다. 파일·네트워크 변경 없음."""
import pytest

from scripts.check_tool_catalog import _release_heading_problems


def notes(heading, body=""):
    return f"# Release Notes\n\n## {heading}\n\n{body}\n"


@pytest.mark.parametrize("ko,en", [
    ("미배포 — 2026-10-01", "Unreleased — 2026-10-01"),
    ("v2.7.3 · beta — 2026-09-21", "v2.7.3 · beta — 2026-09-21"),
    ("미배포 — 2026-10-01", "미배포 — 2026-10-01"),
    ("Unreleased — 2026-10-01", "Unreleased — 2026-10-01"),
    (" 미배포 — 2026-10-01 ", "Unreleased — 2026-10-01  "),
])
def test_equivalent_latest_headings(ko, en):
    assert _release_heading_problems(notes(ko), notes(en)) == []


@pytest.mark.parametrize("ko,en", [
    ("미배포 — 2026-10-01", "Unreleased — 2026-10-02"),
    ("v2.7.3 · beta — 2026-09-21", "v2.7.2 · beta — 2026-09-21"),
    ("v2.7.3 · beta — 2026-09-21", "v2.7.3 · stable — 2026-09-21"),
    ("미배포 — 2026-10-01", "v2.7.3 · beta — 2026-10-01"),
    ("미배포예정 — 2026-10-01", "Unreleased — 2026-10-01"),
    ("미배포 — 2026-10-01 추가", "Unreleased — 2026-10-01"),
])
def test_real_status_version_date_or_title_mismatches_fail(ko, en):
    problems = _release_heading_problems(notes(ko), notes(en))
    assert len(problems) == 1
    assert "최신 제목 불일치" in problems[0]


@pytest.mark.parametrize("ko,en,missing", [
    ("# 문서\n", notes("Unreleased — 2026-10-01"), "RELEASE_NOTES.md"),
    (notes("미배포 — 2026-10-01"), "# Notes\n", "RELEASE_NOTES_ENG.md"),
    ("# 문서\n##   \n", notes("Unreleased — 2026-10-01"), "RELEASE_NOTES.md"),
])
def test_missing_latest_section_is_not_silently_skipped(ko, en, missing):
    assert _release_heading_problems(ko, en) == [f"{missing}에 최신 섹션 없음"]


def test_both_missing_headings_fail():
    assert len(_release_heading_problems("# 문서", "# Notes")) == 2


@pytest.mark.parametrize("matching_text", [
    "See 미배포 — 2026-10-01 in the source notes.",
    "## 미배포 — 2026-10-01\n\nHistorical section.",
    "## Unreleased — 2026-10-01\n\nHistorical section.",
])
def test_matching_body_or_old_section_cannot_mask_latest_mismatch(matching_text):
    assert _release_heading_problems(
        notes("미배포 — 2026-10-01"),
        notes("v2.7.3 · beta — 2026-09-21", matching_text),
    )



def test_crlf_line_endings_have_the_same_heading_contract():
    assert _release_heading_problems(
        notes("미배포 — 2026-10-01").replace("\n", "\r\n"),
        notes("Unreleased — 2026-10-01").replace("\n", "\r\n"),
    ) == []


def test_empty_latest_heading_cannot_be_hidden_by_older_valid_heading():
    assert _release_heading_problems(
        "# 문서\n## \n## 미배포 — 2026-10-01\n",
        notes("Unreleased — 2026-10-01"),
    ) == ["RELEASE_NOTES.md에 최신 섹션 없음"]


@pytest.mark.parametrize("fake", [
    "```markdown\n## Unreleased — 2026-10-01\n```\n",
    "~~~markdown\n## Unreleased — 2026-10-01\n~~~\n",
    "<!--\n## Unreleased — 2026-10-01\n-->\n",
    "> ## Unreleased — 2026-10-01\n\n",
    "    ## Unreleased — 2026-10-01\n\n",
])
def test_fake_markdown_headings_cannot_mask_real_latest_mismatch(fake):
    assert _release_heading_problems(
        notes("미배포 — 2026-10-01"),
        fake + notes("v2.7.3 · beta — 2026-09-21"),
    )


def test_unspaced_empty_latest_heading_fails_instead_of_using_old_heading():
    assert _release_heading_problems(
        "##\n\n## 미배포 — 2026-10-01\n",
        notes("Unreleased — 2026-10-01"),
    ) == ["RELEASE_NOTES.md에 최신 섹션 없음"]


@pytest.mark.parametrize("heading", [
    "   ## Unreleased — 2026-10-01 ###\n",
    "Unreleased — 2026-10-01\n--------------------------\n",
])
def test_valid_markdown_heading_forms_keep_translation_equivalence(heading):
    assert _release_heading_problems(notes("미배포 — 2026-10-01"), heading) == []
