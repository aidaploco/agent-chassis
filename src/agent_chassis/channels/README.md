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
  key `{conversation_id}:{kind}:{sequence}`) and the `OutboundSender` protocol.
  Outbound activities are at-least-once, so **senders MUST suppress duplicate
  keys** (D15).
- [`http.py`](http.py) — the HTTP channel and the FastAPI app factory. Endpoints:
  `/v1/messages` (chat), `/v1/tasks` + `/v1/tasks/{id}` (kick off / poll a task),
  `/v1/approvals` (approve a paused task), `/v1/self` (introspection),
  `/healthz`. Also home to `send_to_conversation` (the update-with-start call)
  and the root turn trace span. Auth is D21 (see below).
- [`slack.py`](slack.py) — the Slack-shaped webhook router (ack fast, run the
  turn in the background) and `SlackOutboundSender` (LRU-deduped push delivery).
  Tested against a fake; a real Slack integration swaps the sender's URL/auth
  and nothing else.

## Authentication (D21)

The audited identity is **never caller-supplied**. Bearer tokens map to
principals via `HTTP_BEARER_TOKENS`. When it's unset the posture splits: the
identity-carrying endpoints (`/v1/tasks`, `/v1/approvals`) refuse to serve
(503), while `/v1/messages` and `/v1/self` serve anonymously for local dev —
fail-closed where identity matters, fail-noisy where it doesn't. Slack inbound
verifies the signing-secret HMAC when `SLACK_SIGNING_SECRET` is set.
