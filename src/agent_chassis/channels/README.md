# `channels/` — inbound/outbound channel adapters (D3, D15)

Channels are the edge of the system. Their whole job is to **normalize a wire
format to and from `MessageEnvelope`** so the core never learns which channel
it is on.

## Two channel styles, one ingestion mechanism

Both styles deliver a message the same way — Temporal **update-with-start**
(D3), which starts the conversation workflow on the first message and attaches
to the running one after that. They differ only in how the reply gets back:

| Style | Example | Reply path |
|---|---|---|
| **Sync** | HTTP | The reply rides the update result — request/response. |
| **Async** | Slack-shaped | The webhook is ack'd immediately; the reply is *pushed* later via a worker-side `OutboundSender` (an outbound activity, D15). |

## Files

- [`base.py`](base.py) — `OutboundDelivery` (carries the structured idempotency
  key, e.g. `{conversation_id}:turn:{n}` for a reply) and the `OutboundSender`
  protocol. Outbound activities are at-least-once, so **senders MUST suppress
  duplicate keys** (D15), and a key is only remembered once the send succeeded,
  so a failed send's retry is not mistaken for a duplicate.
- [`http.py`](http.py) — the HTTP channel and the FastAPI app factory. Endpoints:
  `/v1/messages` (chat), `/v1/tasks` (kick off a task; `GET` lists this Employee's
  recent runs, scoped by its task queue) + `/v1/tasks/{id}` (poll one),
  `/v1/approvals` (approve a paused task), `/v1/self` (introspection),
  `/healthz`. An unknown task or workflow id is a 404. Also home to
  `send_to_conversation` (the update-with-start call) and the root turn trace
  span. Auth is D21 (see below).
- [`records.py`](records.py) — `GET /v1/audit` and `GET /v1/outcomes`: read-only,
  authenticated, newest first, bounded, and scoped to this Employee by the
  manifest's name (never by a query parameter). The api opens the stores
  read-only (no DDL); an unavailable store is a 503, not a 500.
- [`slack.py`](slack.py) — the Slack-shaped webhook router (ack fast, run the
  turn in the background; mounted only when the manifest lists `slack`) and
  `SlackOutboundSender` (LRU-deduped push delivery). One class serves two
  purposes: customer replies derive their Slack channel from the conversation id
  (`slack-C42` posts to `C42`), while the **approvers' sender** is built with
  `derive_channel=False` and its own webhook (`SLACK_APPROVERS_WEBHOOK_URL`), so
  an internal escalation notice can never be addressed to a customer's channel.
  Tested against a fake; a real Slack integration swaps the sender's URL/auth
  and nothing else.

## Authentication (D21)

The audited identity is **never caller-supplied**. Bearer tokens map to
principals via `HTTP_BEARER_TOKENS`. When it's unset the posture splits: the
identity-carrying endpoints (`/v1/tasks`, `/v1/approvals`) refuse to serve
(503), while `/v1/messages` and `/v1/self` serve anonymously for local dev —
fail-closed where identity matters, fail-noisy where it doesn't. Once tokens are
configured, `/v1/messages` also takes the message's `sender` from the token's
principal and ignores the request body's `sender` field (which only names the
sender in the anonymous local-dev posture). Slack inbound verifies the
signing-secret HMAC when `SLACK_SIGNING_SECRET` is set.
