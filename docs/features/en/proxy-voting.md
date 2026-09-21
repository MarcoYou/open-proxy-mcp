# AGM Analysis and Proxy Voting Recommendations

**Review the agenda and supporting evidence before an Annual General Meeting (AGM) or an Extraordinary General Meeting (EGM).** OPM structures the notice, evaluates individual agenda items and candidates, and returns voting recommendations with reasons, policy citations, and filing excerpts.

## Read the result

| Status | Meaning | Next step |
|---|---|---|
| FOR | The engine supports the item on the evidence it evaluated. | Check the rationale and original filing; this is not a blanket endorsement. |
| AGAINST | A clear trigger was found, such as disqualification, a mandatory-law breach, complete capital impairment, or a qualified/adverse audit opinion or disclaimer of opinion. | Review the cited trigger and its applicability to this meeting. |
| REVIEW | A concern, condition, competing proposal, or unresolved judgment needs review. | Read the reason and decide using the evidence; REVIEW is not an instruction to abstain. |
| NO_VOTE | The item is not subject to a vote, for example a withdrawn item or a report-only matter. | Keep it in the agenda record without treating it as a voting recommendation. |
| NO_DATA | The engine could not obtain or structure enough information to judge the item. | Read the attached source excerpt, expand it if needed, and verify the original. Missing data never means automatic approval. |

These are the structured decision values. The default report uses Korean labels; ask your connected AI to explain it in English while preserving the statuses, caveats, and citations.

## Policy and evidence

Judgments use OPM's **Open Proxy Guideline**: minority-shareholder protection, governance transparency, long-term value, and traceability. **The policy's stated opposition criteria and the engine's automatic AGAINST triggers are different.** Concerns such as long tenure or weak performance generally produce REVIEW unless a separate hard trigger applies. A five-year tenure warning alone does not produce AGAINST. Board attendance is a policy criterion but is not currently an engine trigger.

Ask for the policy section cited in a recommendation, or use `proxy_guideline(section="2.4")`. The policy-to-engine mapping is in `proxy_guideline(section="0-A")`; the [policy source](../../../open_proxy_mcp/data/guideline/open-proxy-guideline.md) and [technical reference](../../../wiki/tools/proxy_advise_before_meeting.md) are in Korean.

- Inside-director performance evaluation is limited to reappointment candidates and their registered board tenure. Weak performance alone leads to REVIEW.
- Cumulative voting, available seats, competing candidates, conditional proposals, and parent/child agenda relationships matter. Individual recommendations must be read together with the election constraints.
- Evidence includes filing references and excerpts. An uncertain source location is flagged: confirm that the excerpt belongs to the item before using it. Truncated excerpts can be expanded; a filing link alone does not prove that the right section was matched.
- Recommendations use filing evidence. News checks are separate: `director_news` can surface candidate-related articles, but a keyword hit is not a verified fact or a voting verdict.

## Select the meeting and analysis date

By default, `meeting_type="auto"` selects an annual or extraordinary meeting. Use `annual` or `extraordinary` to restrict the type; `year` means the meeting year, not the financial year. Always check the selected meeting and selection rationale. If an explicitly requested type has no matching notice, the response reports `no_filing` and offers available notice references instead of inventing a meeting.

`as_of="YYYYMMDD"` sets the information cutoff. By default, a meeting dated today or earlier uses the day before that meeting; a future meeting uses today in Korea. If its date cannot be read, the tool uses today and warns that historical analysis may include later information. `include_after_meeting=True` admits later filings with warnings, but the selected meeting's voting results remain excluded from the recommendation. Use [Meeting Agenda and Results](meeting-agenda.md) for the actual outcome.

## Ask it like this

> "Review LG Chem's 2026 AGM agenda in English. Keep each decision status and show its rationale, policy section, and filing evidence."
>
> "Review the upcoming extraordinary general meeting. Identify the selected meeting, competing nominees, available seats, and any cumulative-voting constraints."
>
> "Are there grounds to vote against this director election? Explain which concerns are REVIEW, which are hard triggers, and which checks were not performed."
>
> "Reconstruct this meeting's recommendations using only filings available by 20260320. Separate missing data from items that do not require a vote."

## See also

- [Meeting Agenda and Results](meeting-agenda.md) — notices, candidates, and actual outcomes
- [Financials](financials.md) — the financial context for recommendations
- [Shareholder Return](shareholder-return.md) — dividend and treasury-share background
