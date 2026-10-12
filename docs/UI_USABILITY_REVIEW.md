# Final QA usability review — recommendations only

Date: 2026-10-13. Reviewed the actual localhost browser with the seven-span instrumented
Agent, concurrent timing fixture and fresh HTTP/PostgreSQL baseline/candidate executions.
No UI redesign was performed. This is an engineering walkthrough, not a novice-user study.

The interface exposes the necessary evidence and shared navigation works, but the default
Diagnostics/Span Details view requires considerable reading and scrolling, especially in
a narrow browser pane. Outcome prose, rule categories (including zero counts), identifiers,
JSON, exception stack traces and proposal qualifications compete with the affected step.
Several separate “Inspect” actions are useful but difficult to distinguish at first glance.

| User question | Current observation | Recommended default |
| --- | --- | --- |
| What happened? | Recorded outcome is visible, followed by several technical paragraphs | One short recorded-outcome sentence and a compact failure/warning summary |
| Which step failed or was slow? | Finding cards and graph/timeline expose it, but long category lists come first | Lead with the affected step name, operation badge, status and measured duration |
| Why was it flagged? | Rule explanation and parameters are available, but rule IDs add visual noise | Plain-language recorded evidence first; expand for rule ID, thresholds and coverage |
| What should I do next? | Suggestions are explicit, review-only and detailed | A concise proposed action with an uncertainty label; expand for risks, preconditions and validation |
| How do I reach the step? | Evidence and suggestions correctly share selection with graph/timeline/details | One consistent “Go to step” action, clearly indicating a selected or filtered-out step |

Recommended priorities for a separately approved UI task:

1. Make “What happened / affected step / recorded evidence / next action” the default
   reading order. Preserve the distinction between failed operations and recovered execution.
2. Put Trace/Span IDs, UTC timestamps, raw attributes/events, stack traces and rule parameters
   under expandable technical details, with copy actions where useful. Keep status, duration,
   exception type and parent navigation immediately available.
3. Collapse zero-count categories and repeated explanatory text. Retain visible warnings for
   unknown outcomes, missing measurements and partial coverage; never replace them with success.
4. Keep proposal evidence, risks, missing evidence and validation requirements accessible.
   Label proposals “Not applied”; a suggested action must not look like an executed repair.
5. Make selected-step visibility and jump behavior explicit when filters hide a referenced
   span. Consider a deliberate “Reveal step” action without silently changing user filters.
6. Separate SDK-recorded span duration from evaluator worker duration. Zero/unknown durations
   and partial token coverage need compact, explicit labels rather than inferred values.

Acceptance for any later redesign should include novice walkthroughs, keyboard navigation,
narrow/wide layouts, and the existing stale-response/graph-selection regressions. Do not
remove evidence or imply confirmed causes, semantic correctness or real-world improvement.
