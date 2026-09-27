# legacyflow-api

LegacyFlow modular-monolith API (FastAPI, Python 3.12). Slice 1: foundation —
app bootstrap, Supabase JWT verification, `GET /v1/me`, domain scaffolding
(identity/firms/matters), RFC 7807 errors.

## Run

```bash
cd services/api
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8000
```

Docs: http://localhost:8000/docs · Health: http://localhost:8000/healthz

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `SUPABASE_URL` | prod | e.g. `https://xyz.supabase.co`; derives JWKS URL + `iss` |
| `SUPABASE_JWKS_URL` | no | override JWKS endpoint (default `{SUPABASE_URL}/auth/v1/.well-known/jwks.json`) |
| `SUPABASE_JWT_AUDIENCE` | no | expected `aud` claim (default `authenticated`) |
| `DATABASE_URL` | no | Postgres DSN; when unset `GET /v1/me` returns HTTP 501 `profile_store_unavailable` |
| `TEST_JWT_ISSUER` / `TEST_JWT_SECRET` | tests only | enables the HS256 test-override auth path; never set in production |
| `API_ENV` | no | environment label (default `development`) |

No secrets are committed; configure via environment only.

## Quality gates

```bash
ruff format --check . && ruff check .
mypy app
pytest
```

## Slice-1 notes

- Routers are thin (`app/api/v1/endpoints/*`); behavior lives in `app/domains/*` services.
- `firms` and `matters` services are typed stubs raising `NotImplementedError("slice 2")`.
- No legal content, AI, documents, uploads, or payments in this service.
