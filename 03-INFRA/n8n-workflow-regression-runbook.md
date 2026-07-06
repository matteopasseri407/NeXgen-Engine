---
tags:
  - infra
  - n8n
  - runbook
status: active
type: runbook
---

# n8n Workflow Anti-Regression Runbook

Mandatory procedure for modifying, publishing, or diagnosing any n8n workflow. The rules apply to every workflow: trigger, transformation, LLM/API, parser, persistence, external output.

## Core rule

A `success` execution does not prove the screener actually worked.
Verification is only valid if the production execution completes its real chain end-to-end.

The `execute_workflow` MCP command can report that the start was accepted while the execution is still `running`, suspended, or later cancelled.
Always check `execution_entity.status`, `stoppedAt`, and the node data.

## Publishing on n8n 2.x

Scheduled executions use the snapshot in `workflow_history` pointed to by `activeVersionId`.
Editing only `workflow_entity.nodes` does not update production.

Procedure:

1. Export and save a backup of the workflow.
2. Create a new `versionId` and a new row in `workflow_history`.
3. Update the draft in `workflow_entity`.
4. Explicitly publish the new version.
5. Verify that `versionId = activeVersionId`.
6. Re-check cron, timezone, and `active=true`.

## Prompt and LLM parser contract

For every LLM node with dynamic text:

- `parameters.text` must start with `=`.
- The prompt tail must contain the dynamic data expression, not `{{ ... }}` as literal text.
- If the model is configured for `json_object`, the parser must accept pure JSON `{ ... }`.
- Prompt and parser must use the same format. Never combine pure JSON with a parser based only on legacy delimiters.
- The prompt must enforce: first character `{`, last character `}`, no reasoning, no Markdown, no external text.

Regression signal:

- the batch node contains real data;
- the LLM node returns `analyzed: 0`;
- or it returns free-form reasoning and no JSON.

## Reasoning-capable LLM judges

"Reasoning" models normally put their thinking in `reasoning_content` and the JSON in `content`. But under token-budget pressure the reasoning **leaks back into `content` and truncates the JSON**. Two mandatory defenses, always together:

1. **Token headroom.** A generous `maxTokens`. The cost is negligible, and JSON truncation disappears.
2. **Tolerant parser.** Never `JSON.parse` directly on the output. Extract the JSON via brace-matching (ignoring text/reasoning before or after it) and, if truncated, recover the individual complete objects via regex. Mark "batch failed" ONLY if no object at all can be recovered. That way a model hiccup never loses data or triggers a false alert.

Also make critical external nodes (LLM calls, DB writes, notifications) non-fatal with `onError: continueRegularOutput` + `retryOnFail`, so an outage turns into a readable outcome instead of a crash that trips the error workflow.

## Checks for batching workflows

- Pick a batch size that avoids both LLM output truncation and exceeding the MCP window (typically 5 minutes). Test with real volumes.
- Deduplicate within the run before batching, on the relevant keys.

## Deduplication and writes

Never write to the database before the parser has produced a valid verdict.
A failed LLM batch must never silently turn every item into a `PASS`.

Before a verification run:

- back up the rows you intend to remove;
- delete only the keys contaminated by the faulty run;
- never blindly flush global caches.

Deduplicate within the same execution, in addition to against the database.

## External outputs

If the workflow's contract includes a notification or a message, every run must produce an observable outcome even when it finds nothing useful.
The summary must read its statistics directly from the nodes that produce them, not from `pairedItem` metadata after LLM batching.

The message must contain real numbers: items received, analyzed, discarded, duplicates, passed the gate, evaluated by the LLM, outcomes per category, API errors, and failed batches.

Delivery is verified only by a positive response from the external system.

## Telling a genuine empty result from a broken pipeline

Genuinely zero:

- the sources produced data, or an explicit count of zero;
- the gate has numeric statistics;
- the `analyzed` sum across LLM batches matches the items sent to the LLM;
- every batch contains valid JSON;
- `has_partial_failures=false`;
- the parser produces numeric counts;
- the notification responds `ok=true`.

Broken pipeline:

- `N/A` in the summary;
- items in the batches but `analyzed: 0`;
- items sent to the LLM with no JSON output;
- `success` with the notification node not executed;
- execution still `running` or cancelled;
- false `PASS` values created as a fallback for unparseable batches;
- `versionId` different from `activeVersionId`.

## Acceptance gate before leaving the schedule active

Do not call a change done until every point below is true:

1. Backup exists.
2. New version published.
3. `activeVersionId` correct.
4. Cron and timezone verified.
5. Production run with `status=success` and `stoppedAt` populated.
6. Every LLM batch parseable.
7. Input item count equals evaluated item count, except for explicitly counted dedup/discards.
8. No partial errors.
9. Database writes consistent.
10. Notification `ok=true`, `message_id` present, no `N/A`.

## Rule: LLMs inside healthchecks

In operational healthchecks, the LLM may translate the alert, but must not decide the status.
The gate must stay deterministic and produce minimal facts: workflow, active state, HTTP/status, execution or verifiable counts when available.
The LLM node must sit downstream of the gate and must have a technical fallback: if the model, gateway, or parser fail, the alert is still sent with the deterministic text.

## Live verification without the n8n MCP

`n8n execute` from the CLI inside the production container can fail: it collides on the Task Broker, and in `internal` mode it does not initialize the license provider, so credential decryption fails. So the CLI cannot verify nodes that use credentials. For end-to-end verification, use the live instance instead: publish a temporary version with a cron a few minutes out, restart, let the real run fire, then restore `activeVersionId` to the definitive version and delete the temporary one.

## Related notes

- `03-INFRA/remote-automation.md`
