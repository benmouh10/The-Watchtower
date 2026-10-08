# GlobeLens AI

**News intelligence platform** — scrapes global media, groups articles into events with vector
similarity, then uses LLMs to synthesize, geolocate, and score what happened.

<!-- markdownlint-disable MD033 -->

```
┌─────────────┐    ┌─────────────┐    ┌──────────────────┐
│  Next.js    │───▶│  FastAPI    │───▶│  PostgreSQL 16   │
│  Frontend   │    │  Backend    │    │  + pgvector      │
│  :3000      │    │  :8000      │    │  :5432           │
└─────────────┘    └─────────────┘    └──────────────────┘
                          │
                  ┌───────┴────────┐
            ┌─────┴─────┐  ┌───────┴──────────┐
            │   Redis   │  │  Elasticsearch   │
            │  :6379    │  │  :9200           │
            └───────────┘  └──────────────────┘
```

## The pipeline

Four stages. Each is a batch worker kicked off by an admin endpoint, and each advances an
article's `processing_status`:

| Stage | Service | What it does | Status |
|---|---|---|---|
| 1. Ingest | `scraper_service.py` | RSS discovery + headless Chromium (Playwright) hydration, BeautifulSoup extraction | `SCRAPED` |
| 2. Vectorize | `embedding_service.py` | 1536-dim embeddings, SHA-256 content-hash cached in Redis | `EMBEDDED` |
| 3. Cluster | `clustering_service.py` | pgvector cosine distance ≤ 0.08 within a 72h window → link or spawn event | `CLUSTERED` |
| 4. Enrich | `llm_service.py` | Summary (≥3 paras), topic, bias lean, country, lat/lon, importance 0–10 → index to Elasticsearch | `PROCESSED` |

Trigger the stages in order:

```bash
curl -X POST /api/v1/articles/sync        -H "Authorization: Bearer $ADMIN_TOKEN"
curl -X POST /api/v1/admin/embed/process  -H "Authorization: Bearer $ADMIN_TOKEN"
curl -X POST /api/v1/admin/cluster/process -H "Authorization: Bearer $ADMIN_TOKEN"
curl -X POST /api/v1/admin/llm/process    -H "Authorization: Bearer $ADMIN_TOKEN"
```

## Features

- **Global event map** — Leaflet map of geo-tagged events, markers sized by importance and
  corroborating source count, with topic filtering
- **AI-synthesized event dossiers** — cross-source summary with per-paragraph citation
  tooltips, aggregated source credibility, and a bias-distribution slider
- **Fact-checker** — submit a URL or claim, get a credibility score, trust risks, per-claim
  status, and historical matches
- **AI News-Scout chatbot** — retrieval over the indexed events; falls back to live web search
  (Tavily) when the answer isn't in the database, and tells you which it used
- **Full-text search** — Elasticsearch with an `edge_ngram` autocomplete field
- **Admin dashboard** — live service health, pipeline funnel, media/bias/topic distributions

## Tech stack

| Layer | Technology |
|---|---|
| Web framework | FastAPI 0.111 + uvicorn (async-first, auto OpenAPI docs) |
| ORM / migrations | SQLAlchemy 2.x async (asyncpg) + Alembic 1.13 (psycopg2) |
| Database | PostgreSQL 16 + pgvector, IVFFlat cosine index |
| Cache | Redis Stack |
| Search | Elasticsearch 8.13 |
| LLM providers | NVIDIA NIM · Azure · Grok · Gemini (switchable via `LLM_PROVIDER`) |
| Embeddings | Azure · OpenAI · Grok · Gemini (switchable via `EMBEDDING_PROVIDER`) |
| Web search | Tavily |
| Frontend | Next.js 14 (App Router) + React 18 + Tailwind + Leaflet + Recharts |
| Container | Docker + Docker Compose (5 services, health-checked) |

> `ANTHROPIC_API_KEY` is declared in `config.py` for forward compatibility, but the
> `anthropic` provider path is **not implemented** — no code calls it.

## Getting started

```bash
git clone https://github.com/benmouh10/The-Watchtower.git
cd The-Watchtower

cp .env.example .env      # then fill in the API keys you need
docker compose up --build
```

| Service | URL |
|---|---|
| Frontend | http://localhost:3000 |
| API docs (Swagger) | http://localhost:8000/docs |
| Health probe | http://localhost:8000/health |
| Elasticsearch | http://localhost:9200 |
| Redis Insight | http://localhost:8001 |

Apply migrations on first boot:

```bash
docker exec -it globelens_backend alembic upgrade head
```

Seed the publishers and an admin account:

```bash
docker exec -it globelens_backend python -m app.seed_data
```

## API surface

All routes are under `/api/v1`. Implemented endpoints:

| Area | Endpoints |
|---|---|
| Auth | `POST /auth/register` · `POST /auth/login` · `GET /auth/me` |
| Events | `GET /events` · `GET /events/map` · `GET /events/{id}` |
| Search | `GET /search/query` · `GET /search/autocomplete` |
| Fact-check | `POST /fact-check` · `GET /fact-check/{id}` · `GET /fact-check/users/{id}` |
| Chatbot | `POST /chatbot` |
| Pipeline | `POST /articles/sync` · `POST /admin/{embed,cluster,llm}/process` |
| Admin | `GET /admin/stats` · `POST /admin/search/sync` · `GET/POST /admin/threat/blocks` · `POST /admin/threat/blocks/{ip}/unblock` |

Auth is a JWT (HS256, 30 min) with four roles: `GUEST` → `AUTH_USER` → `JOURNALIST` → `ADMIN`.

> Several controllers still contain placeholder stubs (`/events/trending`, `/events/latest`,
> `/comments/*`, `/auth/refresh`, and most of `/admin/*`). They return empty or hardcoded
> payloads and are marked as such in the source.

## Security status

Read this before exposing the stack beyond localhost:

- **Incomplete authorization.** Most `/api/v1/admin/*` routes are stubs that do **not** check
  the `ADMIN` role. `DELETE /admin/users/{id}` and `PUT /admin/users/{id}/role` are live paths
  with no guard. Only `/admin/stats`, `/admin/{embed,cluster,llm}/process`,
  `/admin/search/sync`, and the `/admin/threat/*` routes are actually protected.
- **Placeholder token.** `POST /auth/refresh` returns the literal string
  `new_placeholder_jwt_token` and requires no authentication.
- **Weak committed defaults.** `SECRET_KEY`, `POSTGRES_PASSWORD`, and `REDIS_PASSWORD` fall
  back to publicly known values in `config.py` and `docker-compose.yml`, and Elasticsearch runs
  with `xpack.security.enabled=false`. The stack starts insecure if `.env` is incomplete.
- **Hardcoded admin seed.** `app/seed_data.py` and `app/create_admin.py` create
  `admin@globelens.ai` with the password `adminpass123`, and `create_admin.py` *resets* the
  password and re-grants ADMIN on any existing account with that address.
- **Unverified LLM fallbacks.** When the LLM provider is unreachable, the fallbacks in
  `llm_service.py` synthesize plausible-looking summaries, coordinates, credibility scores, and
  historical matches that are indistinguishable from real output to API consumers.
- **Token storage.** The frontend keeps the JWT in `localStorage` and a JS-writable cookie
  (no `HttpOnly`), and `middleware.ts` gates `/admin/*` on cookie *presence* only.

## Threat Lens (innovation S5)

Threat Lens scores live request telemetry for anomalous patterns (missing user
agent, oversized bodies, repeated auth failures, …) and, when a client trips the
risk threshold enough times inside a short window, blocks it at the edge: every
subsequent request from that identity answers `403` with a friendly body until the
block expires. It is built around a synthetic forest trained to model *normal* traffic
only — risk is the percentile a request's feature vector earns against those normal
scores, so the model never needs real abuse samples.

### Architecture

| Piece | Where |
|---|---|
| Block enforcement + telemetry | `backend/app/middleware/threat_lens.py` — checks the Redis marker, then fires one scored event onto the `threat` queue (off the request path, fail-open) |
| Scoring | Celery task `app.worker.threat_tasks.analyze_telemetry_task`, consumed by the dedicated autoscaling `threat` compose service (main worker stays on the default queue) |
| Features + model | `backend/shared_core` (`features.py`, `threat_model.py`, `AnomalyDetector`) |
| Block state | Redis markers `threat:blocked:{ip}` (403 enforcement, TTL), `threat:window:{ip}` (anomaly window), `threat:blocks` feed; durable audit row in `blocked_ips` |
| Scoring sidecar | `threat` service exposes `GET :9000/health` and `GET :9000/score` (body `{n_header, no_user_agent, unusual_method, request_rate, body_size_bytes}`) for live model verification |

### Behavior

- **Fail-open everywhere.** Any Redis/scoring/model glitch logs and lets the request
  through. A fresh checkout (`THREAT_LENS_ENABLED=false`) is completely inert.
- **No single-request lock-outs.** An identity needs `THREAT_LENS_WINDOW_MIN_ANOMALIES`
  anomalies within `THREAT_LENS_WINDOW_SIZE` events before the marker is written; a
  noisy legit request can never ban a real user.
- **Block lifecycle.** The worker writes marker + feed entry + `blocked_ips` row
  (reason `anomalous_x{count}_risk_{r}`). Unblocking clears all of it; the feed entry
  survives as history.
- **Identity pinning.** Under a reverse proxy the client is identified via
  `X-Forwarded-For` when `RATE_LIMIT_TRUST_FORWARDED=true`, otherwise by socket peer.

### Admin self-serve

| Route | What it does |
|---|---|
| `GET /api/v1/admin/threat/blocks` | `blocked_ips` rows (newest first, `limit`/`offset`) + live `active` flag per identity (Redis marker present?) + recent feed entries |
| `POST /api/v1/admin/threat/blocks` | operator block `{"ip": ..., "reason": ...}` → marker + feed + `blocked_ips` row with `source=MANUAL` |
| `POST /api/v1/admin/threat/blocks/{ip}/unblock` | revoke — clears marker, anomaly window, and row; `404` when nothing exists for the identity |

The whole admin router (including these routes) is gated by the `require_admin`
router dependency, so they inherit the `ADMIN`-only policy the policy test enforces.

### Training & provisioning

```bash
# Retrain the forest against committed code (synthetic normal-only + evaluation)
docker compose exec -T backend python -m app.threat_lens_train \
  --out /app/models/threat_lens.joblib
```

The artifact lives on the shared `model_data` volume that both the backend and the
`threat` container mount at `/app/models` (same image, same artifacts). Drop the file
there and the sidecar loads it self-healingly — the detector re-checks on demand if a
container boots before the artifact lands (`model_loaded` flips from `false` to `true`
on `:9000/health` without a restart).

### Operator knobs (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `THREAT_LENS_ENABLED` | `false` | master switch for telemetry sampling + enforcement |
| `THREAT_LENS_RISK_THRESHOLD` | `0.90` | risk percentile that counts as an anomaly |
| `THREAT_LENS_WINDOW_MIN_ANOMALIES` | `2` | anomalies required inside the window to block |
| `THREAT_LENS_WINDOW_SIZE` | `5` | rolling window of scored events per identity |
| `THREAT_LENS_BLOCK_TTL_SECONDS` | `3600` | block marker / audit expiry |
| `THREAT_LENS_RATE_WINDOW_SECONDS` | `60` | window over which `request_rate` is measured |
| `THREAT_SCORE_PORT` | `9000` | sidecar port (compose) |

> **Worker registration caveat.** `app/worker/celery_app.py` imports
> `app.worker.threat_tasks` explicitly after `autodiscover_tasks(["app.worker"])` —
> Celery only auto-imports modules literally named `tasks`. Without that import every
> telemetry message dies at the consumer with `KeyError` and requeues forever.

## Repository layout

```
backend/          FastAPI app (controllers → services → repositories → entities)
  alembic/        Migrations; 0001 is hand-authored to control extension order
  app/            Application code, plus ad-hoc test_*.py scripts and seed/repair utilities
frontend/         Next.js 14 web client
globe/            Standalone offline cross-lingual pipeline (local models + Ollama + Qdrant).
                  Not part of docker-compose; see globe/README.md
globelens-ai-animation/   Isolated animated brand-logo sandbox
stitch_globelens_ai_intelligence_platform/
                  14 design screens + DESIGN.md — the source of truth the frontend was ported from
scripts/init-db.sql       First-boot Postgres init (installs pgvector, uuid-ossp, pg_trgm)
```

`AGENT_MEMORY.md` is an internal development log with the phase-by-phase build history and the
reasoning behind the schema and infrastructure decisions.
