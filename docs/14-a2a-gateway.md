# 14 · A2A v0.3 JSON-RPC task gateway

**Status:** Initial implementation, testable locally and in CI; not yet interoperated with a real Muse, Codex Cloud, or Google AI agent. The A2A gateway is **not an LLM worker** and does not wake up any third-party platform.

## Scope and standard

This gateway implements the **A2A v0.3.0 JSON-RPC core** for text-only asynchronous project task coordination:

- Discovery via `GET /.well-known/agent-card.json` (A2A v0.3.0), advertised with Bearer authentication, `streaming=false`, and `pushNotifications=false`.
- `POST /a2a` with JSON-RPC methods `message/send`, `tasks/get`, and `tasks/cancel`.
- **No** streaming (`message/stream`), history of state transitions, push notifications, or authenticated extended Agent Card. These capabilities are not advertised.
- Only **new** task messages are accepted, not follow-up messages to an existing A2A task.
- Project and task logic is the existing PostgreSQL-backed OtterNest domain layer, not a second A2A task store.

Source specification: <https://a2a-protocol.org/v0.3.0/specification/>. This is a scoped gateway, **not a claim of complete certification for every A2A transport or version**; interoperability with official SDK clients remains a separate verification step.

## Discovery configuration

Set `OTTERNEST_A2A_BASE_URL` to the **trusted external HTTPS origin** through which the A2A endpoint is exposed, for example `https://agent.example.invalid`. Local development may use `http://localhost:8000`. This URL is never generated from the incoming `Host` header. Without a valid URL the public Agent Card returns HTTP 503 rather than advertising a false endpoint.

The Agent Card intentionally contains **no project details, agent roster, credentials, or private capabilities**. The A2A POST endpoint uses the same Bearer credentials and project authorization as the REST API.

## Request contract

An A2A caller must already have a valid OtterNest Bearer token, issued out of band. An agent also needs a `manager` membership in the specified project. Workers/reviewers/advisors cannot create tasks via A2A. Only the owner can request R2/R3 work; R3 never bypasses owner approval.

`message/send` requires:

- A standard `message` object with `role: "user"`, a stable `messageId`, and between 1 and 16 text-only `parts` (`kind: "text"`). Total instruction length is capped at 12,000 characters.
- `metadata.projectId` identifying a pre-existing **authorized** project. This is an **OtterNest-specific metadata convention**: generic A2A peers must be configured with their project ID. `message.contextId`, when present, must match the project ID.
- Optional metadata: `title`, `acceptance`, and `risk` (`R0` to `R3`). Default risk is `R1`, and the default acceptance requires owner review.

Example request (the token and project UUID below are placeholders):

```json
{
  "jsonrpc": "2.0", "id": "request-1", "method": "message/send",
  "params": {
    "message": {
      "kind": "message", "role": "user", "messageId": "external-msg-123",
      "parts": [{"kind": "text", "text": "Draft and test the API report"}]
    },
    "metadata": {
      "projectId": "00000000-0000-0000-0000-000000000000",
      "title": "API report", "acceptance": "Report must include test evidence"
    }
  }
}
```

Example transport:

```bash
curl -sS -X POST https://agent.example.invalid/a2a \
  -H "Authorization: Bearer $OTTERNEST_AGENT_TOKEN" \
  -H 'Content-Type: application/json' \
  --data @message.json
```

The returned `result` is an A2A `Task` (not the final answer). `result.id` is the authoritative OtterNest task ID, `contextId` the project ID. `tasks/get` expects `{ "id": "<task-id>" }` and may request `historyLength: 1` to retrieve the accepted original text. Result summary is exposed as a text artifact only after **owner acceptance**, not immediately on worker submission.

## Status and safety semantics

| OtterNest status | A2A task state | Meaning |
|---|---|---|
| ready / waiting_dependencies | submitted | Accepted into central task store, not necessarily running |
| waiting_approval / submitted / blocked | input-required | Human authorization, review or recovery required |
| running | working | An Agent has claimed the task; not proof the remote process can be stopped |
| completed | completed | Result accepted |
| cancelled | canceled | Pending task canceled without assuming a remote worker was halted |

`tasks/cancel` cancels **only unclaimed/pending** or already submitted/blocked tasks. It returns `TaskNotCancelableError` for running and terminal tasks instead of falsely claiming an external process has stopped. A2A task lookup and cancellation are limited to the creating authenticated actor (plus the owner), and always recheck project membership.

## Reliability and data retention

- Inbound `messageId` is deduplicated per project and authenticated actor; a same-ID, different-content replay is rejected.
- The uniqueness rule is enforced by PostgreSQL and serialized under the project row lock. The task, inbox record, outbox event and audit record commit together.
- HTTP `id` is a JSON-RPC request correlation field and is **not** an idempotency key.
- Authentication failures are HTTP errors at the transport layer; valid authenticated JSON-RPC errors use standard JSON-RPC/A2A error codes.
- Do not forward account cookies, secrets, browser sessions or privileged credentials as message text.

## Follow-up interoperability work

Validate the v0.3 card and methods with an official A2A SDK client, verify reverse-proxy HTTPS/TLS auth behavior and rate limiting, and test dispatch to a **real runnable external Agent**. Streaming, push and multi-turn conversations require explicit design and tests before their capability bits can be enabled.

For delegated Agent callers, OtterNest limits the number of **outstanding** A2A-created tasks to **25 per Agent per project**. Replaying an existing message ID does not consume additional capacity. This is an initial safety bound, not a replacement for project-wide budget policies, per-IP rate limits or upstream reverse-proxy request filtering.
