# Sprint 1 — Kanban issue breakdown

Seven granular issues covering the engineering debrief. Create these on the
fork (`benmouh10/GlobeLens_AI`), attach the corresponding commits, and move each
through `Todo → In Progress → Done`.

---

## Issue 1 — Layered backend restructure (`src/{api,services,core,data}`)
**Labels:** `architecture`, `sprint-1`
**Body:**
Reorganise the backend into a strict layered architecture:
`src/api` (routes, schemas, middleware, app assembly), `src/services` (business
logic), `src/core` (config/security/logging/db), `src/data` (ORM entities,
repositories, ETL). Update imports, Alembic, pytest and Docker paths.

**Acceptance criteria**
- [ ] `backend/app` no longer exists; code lives under `backend/src`.
- [ ] `python -m compileall src tests` passes.
- [ ] `uvicorn src.api.main:app` starts and `/health` responds.
- [ ] Docker image and compose mounts updated.

---

## Issue 2 — Enforce thin API routes (no business logic in controllers)
**Labels:** `architecture`, `backend`
**Body:**
Audit every controller. Handlers must only validate input (Pydantic), call a
service, and shape the response. Move any remaining domain logic into
`src/services`.

**Acceptance criteria**
- [ ] Controllers contain no SQL, provider calls, or branching business rules.
- [ ] All request/response bodies typed with Pydantic schemas.
- [ ] Unit tests cover the service boundary.

---

## Issue 3 — Remove ad-hoc/training scripts from the production package
**Labels:** `cleanup`, `backend`
**Body:**
The package root held 12 one-off scripts (`seed_data`, `create_admin`,
`run_llm_direct`, `resynth_clustered`, `fix_zero_coords`, and seven ad-hoc
`test_*.py`). Production code must not contain training/ops scripts.

**Acceptance criteria**
- [ ] ETL/bootstrap scripts live in `src/data/` (or `scripts/`).
- [ ] Ad-hoc `test_*.py` removed from the package (real tests only in `tests/`).
- [ ] No production module imports a script module.

---

## Issue 4 — Add `models/` artifacts loaded once at startup
**Labels:** `mlops`, `backend`
**Body:**
Add `backend/models/` for serialized artifacts (embedding export, fitted
clusterer). Load them once at startup and cache them; no training or model
fitting inside the request path.

**Acceptance criteria**
- [ ] `models/` documented with artifact/loader contract.
- [ ] Loader cached at process startup (no per-request disk/model reads).
- [ ] README states how to reproduce each artifact offline.

---

## Issue 5 — Deployable, pinned service
**Labels:** `devops`, `backend`
**Body:**
Confirm the service builds and runs reproducibly: pinned `requirements.txt`,
multi-stage `Dockerfile`, `/health` returning proper status codes, and the
compose stack healthy.

**Acceptance criteria**
- [ ] `docker compose build backend` succeeds.
- [ ] `GET /health` returns 200 with component status.
- [ ] Requirements pinned; no floating versions in production.

---

## Issue 6 — Automated test coverage for layer boundaries
**Labels:** `testing`, `backend`
**Body:**
Keep the pytest suite green and add coverage for the refactored layers. Fix
time-dependent tests to use relative timestamps instead of a fixed 2026-09-30
base.

**Acceptance criteria**
- [ ] `python -m pytest` green on a clean checkout.
- [ ] Tests do not depend on the wall clock date.
- [ ] Layer import rules covered by at least one test.

---

## Issue 7 — Kanban governance: granular issues + linked commits
**Labels:** `process`
**Body:**
Replace the single "Sprint 1" ticket with the granular issues above. Link each
commit/PR to its issue, keep statuses current, and move completed Sprint 1 work
to **Done**.

**Acceptance criteria**
- [ ] 7 issues created and assigned.
- [ ] Each commit references its issue (`#N`).
- [ ] Board statuses reflect reality; Sprint 1 closed.
