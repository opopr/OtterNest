# 34 · Durable event-driven workflow worker

## Runtime model

- Workflow rules remain owner-created and owner-enabled. The worker is a separate trusted control-plane process: `otternest-workflows`.
- Migration `0018_workflow_cursors.py` adds a durable cursor per rule. Existing rules are backfilled to the current project event head, so deployment does not replay historical events.
- New rules begin at the event head after their creation event. Re-enabling a rule advances its cursor to the current event head; disabled-period events are not replayed.
- Each pass reads events in ascending ID order after that rule's cursor, up to the configured batch size (default 100, maximum 200). Cursor advancement and workflow runs/actions commit in the same PostgreSQL transaction.
- A rule/event pair is additionally protected by the existing unique constraint on `workflow_runs`. Project and rule row locks serialize the worker with manual rule evaluation and concurrent worker instances.
- Failed transactions do not advance the cursor. The worker retries on its next pass. Circuit breakers, cooldowns, task budget enforcement and the two existing actions remain in force.
- Worker actions remain limited to creating a follow-up task or creating an inbox notification. It never wakes a remote Agent or starts a Runner process.

## Run

`otternest-workflows --interval 5 --batch-size 100`

One-shot operator check:

`otternest-workflows --once --batch-size 100`

The worker exits without processing while the system is not in `normal` mode. Project-paused or archived projects are skipped. Use a dedicated service account and the same private PostgreSQL connection configuration as the API.

## Operational notes

- Run after applying migration 0018.
- A backlog is processed in bounded batches; each rule has an independent cursor so a disabled rule does not stall other rules.
- Manual `POST /projects/{project_id}/workflow-rules/evaluate` remains available. Existing unique run records make subsequent worker delivery idempotent.
- This is automatic event-to-task/notification processing, not automatic AI reasoning, remote process wake-up, or third-party side-effect execution.
