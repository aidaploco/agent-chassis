# `skeleton/` — the walking-skeleton Employee

The simplest possible role: one agent (`concierge`), one trivial capability
(`echo`, which reverses text), no routing, no guardrails, no memory. Its
`display_name` is Echo.

**Why it exists (M3.5):** to exercise the *whole* pipeline —
channel → workflow → adapter → outcome event → trace — end-to-end under
docker-compose, **including pod-kill-and-resume**, before any real role work.
It is the default role in [`../../docker-compose.yml`](../../docker-compose.yml),
so `docker compose up` runs this.

## Files

- [`manifest.yaml`](manifest.yaml) — a minimal but complete manifest. Good to
  copy as the starting point for a new role.
- [`packs/echo.py`](packs/echo.py) — one `CapabilityPack` with a single
  `reverse_text` tool marked `pure=True` (no I/O → runs in-workflow instead of
  as an activity). Note it lives *outside* the chassis package on purpose:
  roles are data + packs, never chassis code.

## Try it

```sh
docker compose up --build
curl -s localhost:8001/v1/messages -H 'content-type: application/json' \
  -d '{"conversation_id":"c1","message":"reverse the word chassis"}'
```
