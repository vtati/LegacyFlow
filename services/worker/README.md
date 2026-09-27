# legacyflow-worker

LegacyFlow background worker (Dramatiq + Redis). **Slice 1: scaffold only —
no real jobs are wired yet.**

## Layout

- `worker/settings.py` — `REDIS_URL`, `API_ENV` (pydantic-settings).
- `worker/broker.py` — `get_broker()`: `RedisBroker` when `REDIS_URL` is set,
  otherwise an in-process `StubBroker` with a logged warning (dev/test default).
- `worker/actors.py` — one stub actor, `example_ping`, that logs and returns
  `{"pong": True}`. Clearly marked Slice-1 stub; performs no real work.

## Run / test

```bash
cd services/worker
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `REDIS_URL` | no | e.g. `redis://localhost:6379/0`; when unset the worker uses `StubBroker` |
| `API_ENV` | no | environment label (default `development`) |

## Roadmap

Real jobs (file virus/type processing, OCR/extraction, document assembly,
reminders — artifact §6.5) arrive in Slice 4, along with Redis-backed
retries, idempotency keys, and observability.
