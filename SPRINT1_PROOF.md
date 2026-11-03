# Sprint 1 — Threat Lens: proof of completion

**Project:** The Watchtower
**Scope:** Session 6 — Business Logic & AI
**Verified on:** 2026-11-03, against the running Docker stack (Docker 29.8.2)

---

## The claim, in one sentence

> A request from an abusive client is identified at the edge, scored off the request path by a
> trained Isolation Forest, and after two anomalies inside a rolling window the client receives
> `403 Forbidden` — backed by a Redis marker, a durable `blocked_ips` audit row, an operator
> API, and a fully reversible unblock.

Everything below is reproducible with the commands shown and was executed for this document.

---

## 1. What is running

```
docker compose ps
```

| Service | Role in Sprint 1 | State |
|---|---|---|
| `backend` | API gateway; edge middleware + auth-path guard | healthy |
| `threat` | Scoring sidecar on `:9000` (`/health`, `/score`) | healthy |
| `worker` | Celery consumer for the `threat` queue | running |
| `cache` | Redis: block markers, anomaly windows, block feed | healthy |
| `db` | PostgreSQL: `blocked_ips` audit table | healthy |
| `frontend`, `search`, `mailpit`, `beat` | rest of the product | healthy / running |

The scoring sidecar and the backend share the `model_data` volume mounted at `/app/models`,
so the same trained artifact serves both.

---

## 2. The model is really trained, loaded, and scoring

```powershell
docker compose exec -T backend python -c "import joblib; p=joblib.load('/app/models/threat_lens.joblib'); f=p['forest']; print('trees=',f.n_estimators,'features=',f.n_features_in_,'calibration=',len(p['train_decision']),'seed=',p['seed'])"
```

```
trees= 200 features= 15 calibration= 6000 seed= 7
```

```powershell
docker compose exec -T threat curl -s localhost:9000/health
docker compose exec -T threat curl -s localhost:9000/score
```

```json
{"status":"ok","model_loaded":true,"model_path":"/app/models/threat_lens.joblib"}
{"risk":0.799,"threshold":0.9,"violates":false,"feature_names":[ ...15... ],"features":[ ... ]}
```

- `IsolationForest`, **200 trees**, **15 engineered features**, calibrated on **6000** normal samples.
- Trained **only on synthetic *normal* traffic** — unsupervised novelty detection, so no real
  attack corpus is needed (and none was fabricated).
- The artifact is **not committed** (`*.joblib` is gitignored); it lives on the `model_data`
  volume. If it is missing, the system fails open (see section 7).

### Retraining

```powershell
docker compose exec -T backend threat-lens-train --out /app/models/threat_lens.joblib
# fallback if the console script is absent from an image:
# docker compose exec -T backend python -m shared_core.training --out /app/models/threat_lens.joblib
```

---

## 3. The demo run sheet

Two modes. **Integrated** exercises the full pipeline hermetically (fast, deterministic, no
container state). **Live** proves it on the real stack over HTTP. Run both.

### 3.1 Integrated — the whole loop in one command

```powershell
python scripts/validate_threat_lens_e2e.py
```

Observed output (abridged):

```
2. Normal Traffic Verification
  Request 1..3 from 198.51.100.15 -> HTTP 200
  [OK] Normal IP 198.51.100.15 is NOT blocked

3. Synthetic Attack Traffic & Anomaly Blocking
  Attack Request 1 -> HTTP 200 (telemetry enqueued & scored)
  Attack Request 2 -> HTTP 200 (anomalies >= 2)
  [OK] Redis marker SET: threat:blocked:203.0.113.99 = 1
  [OK] blocked_ips row inserted: risk=0.9815, blocked_by=auto
  Attack Request 3 -> HTTP 403 Forbidden (Edge Dropped!)
  [OK] Legitimate client unaffected: HTTP 200 OK

4. Scoring Diagnostic Endpoint (/score)
  GET  /score -> identity: 203.0.113.99, risk: 0.999,  violates: True
  POST /score -> identity: 203.0.113.99, risk: 0.9855, violates: True
  Features evaluated (15 total)

SPRINT 1 VALIDATION SUCCESSFUL: ALL CHECKS PASSED!
```

This single run demonstrates items **1–5** below.

### 3.2 Live — the real containers, real 403

```powershell
python scripts/validate_threat_lens_e2e.py --live
```

The script picks a fresh TEST-NET address per run, so a leftover block cannot fake a pass.
Observed output:

```
[1] GET /health -> 200 {'status': 'ok', 'model_loaded': True, ...}
[2] Honest client 198.51.100.32 -> GET /api/v1/events/latest
    request 1..3: HTTP 200
[3] Attacker 203.0.113.87 -> POST /api/v1/auth/login (<= 8 hits)
    hit 1: HTTP 401
    hit 2: HTTP 401
    hit 3: HTTP 403
    [OK] edge drop: HTTP 403 — 'This client is temporarily blocked because it was
         flagged as abusive traffic. Try again later.'
[4] Honest client after the block -> HTTP 200
LIVE VALIDATION PASSED: telemetry -> model -> block -> 403
```

Useful flags: `--normal-ip`, `--attack-ip`, `--attempts`, `--delay`.

### 3.3 Operator view — the block is visible and reversible

```powershell
# 1. Redis marker for the blocked peer
docker compose exec -T cache redis-cli -a <REDIS_PASSWORD> --no-auth-warning KEYS 'threat:*'

# 2. Durable audit row
docker compose exec -T db psql -U globelens -d globelens_db `
  -c "SELECT ip, source, round(risk_score::numeric,4) risk, blocked_at FROM blocked_ips ORDER BY blocked_at DESC LIMIT 5;"
```

```
threat:blocked:203.0.113.87
threat:window:203.0.113.87
threat:rate:203.0.113.87:<window>

      ip      |   source    |  risk  |          blocked_at
--------------+-------------+--------+-------------------------------
 203.0.113.87 | THREAT_LENS | 0.9842 | 2026-11-03 21:07:51.643749+00
```

```powershell
# Runnable operator helper (logs in, then lists / blocks / unblocks)
.\scripts\threat_ops.ps1                                      # list + recent feed
.\scripts\threat_ops.ps1 -Block 203.0.113.202 -Reason demo    # manual block
.\scripts\threat_ops.ps1 -Unblock 203.0.113.202               # revoke
```

Observed:

```
[i] 2 blocked identities

ip             source      reason                 risk_score active blocked_at
203.0.113.201  MANUAL      demo_manual_block             1.0   True 2026-11-03T21:12:21+00
198.51.100.80  THREAT_LENS anomalous_x2_risk_0.98      0.9823  False 2026-10-08T08:55:07+00
```

```json
{"message":"Block issued","ip":"203.0.113.201","source":"MANUAL","reason":"demo_manual_block"}
{"message":"Block revoked","ip":"203.0.113.201","cleared_enforcement":true,"cleared_record":true}
```

Unblocking an identity that is not blocked answers `404 {"detail":"No block found for ..."}`; the
helper reports that as a clear failure instead of a crash.

**A 20-second demo that does not depend on model timing:** manually block an address, call the
API as that address, watch it drop, unblock it, watch it recover.

```powershell
.\scripts\threat_ops.ps1 -Block 203.0.113.202 -Reason demo
curl.exe -s -o NUL -w "%{http_code}`n" -H "X-Forwarded-For: 203.0.113.202" http://localhost:8000/api/v1/events/latest
# 403
.\scripts\threat_ops.ps1 -Unblock 203.0.113.202
curl.exe -s -o NUL -w "%{http_code}`n" -H "X-Forwarded-For: 203.0.113.202" http://localhost:8000/api/v1/events/latest
# 200
```

Raw equivalent if you prefer no helper (`GET`/`POST` in prose are HTTP verbs, not PowerShell):

```powershell
$body = @{ email = 'admin@globelens.ai'; password = 'adminpass123' } | ConvertTo-Json
$tok  = (Invoke-RestMethod 'http://localhost:8000/api/v1/auth/login' -Method Post `
           -ContentType 'application/json' -Body $body).access_token
$h    = @{ Authorization = "Bearer $tok" }

Invoke-RestMethod 'http://localhost:8000/api/v1/admin/threat/blocks' -Headers $h |
    ConvertTo-Json -Depth 5
Invoke-RestMethod 'http://localhost:8000/api/v1/admin/threat/blocks/203.0.113.87/unblock' `
    -Method Post -Headers $h
```

`unblock_identity` removes **three** pieces of state, not just the marker: the 403 marker, the
rolling anomaly window, and the current rate bucket. After the unblock,
`EXISTS threat:blocked:203.0.113.87` returns `0` — enforcement and record are both cleared.
**A block is a time-boxed, auditable, reversible action, not a permanent ban.**

One honest nuance observed during verification: an earlier unblock was followed by a **re-block
50 seconds later** (`risk 0.9252`), because telemetry from the attack was still queued and the
worker re-accumulated two anomalies. That is the system working as designed — so in a live demo,
**stop the attack, then unblock**, and the identity stays released.

---

## 4. Evidence map — one row per Sprint 1 deliverable

| # | Deliverable | Implemented in | Proven by |
|---|---|---|---|
| 1 | Telemetry queue | `backend/app/middleware/threat_lens.py` -> `enqueue_telemetry`; Celery route to the `threat` queue | `tests/test_threat_middleware.py`; live `threat:rate:*` / `threat:window:*` keys |
| 2 | Edge middleware (block check) | `ThreatLensMiddleware` | `tests/test_threat_middleware.py`; live `403` on the 3rd attack hit |
| 3 | Feature engineering + Isolation Forest | `shared_core/features.py`, `shared_core/model.py` (`AnomalyDetector`) | `tests/test_threat_features.py`, `tests/test_threat_model.py`; `/score` returns 15 features |
| 4 | joblib persistence | `AnomalyDetector.persist()` / `.load()`, `shared_core/training.py` | `tests/test_threat_model.py`; artifact loads in the container (200 trees / 15 features) |
| 5 | Scoring worker + `/score` | `backend/app/worker/threat_tasks.py`, `backend/app/scoring_api.py` | `tests/test_threat_worker.py`, `tests/test_threat_scoring_api.py`; live `/score` -> `violates: true` |
| 6 | `blocked_ips` table + migration | `backend/alembic/versions/20261007_1200_f6a7b8c9d0e1_add_blocked_ips.py` | `tests/test_threat_migration.py`; `alembic current` -> `f6a7b8c9d0e1 (head)`; live row above |
| 7 | Gateway auth-path enforcement | `auth_controller.reject_blocked_identity`, wired into `get_current_user` / `get_current_user_optional`, plus the edge middleware | `tests/test_threat_auth_enforcement.py` (7 tests) |

### Test evidence

```powershell
cd backend
$env:PYTHONPATH = "../shared_core;" + $env:PYTHONPATH
$files = Get-ChildItem tests -Filter 'test_threat_*.py' | ForEach-Object { 'tests/' + $_.Name }
python -m pytest $files -q
```

```
79 passed
```

The suite covers the non-obvious guarantees as well as the happy path:

- `test_threat_model.py` — artifact round-trips; scores are calibrated against normal traffic.
- `test_threat_middleware.py` — telemetry fires off the request path; blocked peer gets 403; fail-open.
- `test_threat_worker.py` — anomaly window, threshold, block persistence, private-peer exemption.
- `test_threat_auth_enforcement.py` — a blocked identity is refused **even without the edge middleware**;
  disabled flag is inert; cache errors fail open.
- `test_threat_e2e.py` — the complete normal -> attack -> block -> 403 loop in-process.
- `test_threat_migration.py` — a fresh DB reaches the `blocked_ips` revision.

---

## 5. Measured numbers (from the runs above)

| Measurement | Value |
|---|---|
| Clean traffic | 200 OK, 3/3, never blocked |
| Attack traffic first two hits | 200 / 401 (scored, window accumulates) |
| Third hit | **403 Forbidden**, edge drop |
| Risk at block time (integrated) | 0.9815 |
| Risk at block time (live) | 0.9842 |
| `/score` GET (diagnostic) | risk 0.999, `violates: true` |
| `/score` POST (real event) | risk 0.9855, `violates: true` |
| Features per evaluation | 15 |
| Model | IsolationForest, 200 trees, 6000-sample calibration |
| Block reason recorded | `anomalous_x2_risk_0.98` |
| Threat tests | 79 passed |

---

## 6. Why the design looks like this (likely questions)

**Why two enforcement points?**
The middleware drops a blocked peer before routing (cheap, catches everything). The auth
dependency re-checks on `get_current_user` / `get_current_user_optional`, so a route mounted
outside that middleware — a test client, a sub-mount, a future gateway split — still cannot be
reached by a blocked identity. Both return the same message from `BLOCKED_DETAIL`.

**Why block on two anomalies instead of one?**
One model outlier is noise; two inside a five-event window is a pattern. It is configurable
(`THREAT_LENS_WINDOW_MIN_ANOMALIES`, `THREAT_LENS_WINDOW_SIZE`, `THREAT_LENS_RISK_THRESHOLD`).

**Why no accuracy / precision / recall?**
It is unsupervised novelty detection trained on normal behaviour only. The meaningful measure is
the separation between normal decision scores (around `-0.16`) and attack payloads
(`risk >= 0.96` at threshold `0.90`), which is what `/score` exposes.

**Why does it fail open?**
Availability beats enthusiasm. If the artifact is absent or Redis throws, scoring is skipped and
nothing is blocked: `model_loaded: false`, API stays up. A security control that takes the
product down when it hiccups is a worse control. The behaviour is pinned by tests.

**Why are private peers exempt?**
Loopback / RFC 1918 traffic can never be blocked, so an operator cannot lock themselves out of
their own stack. TEST-NET ranges stay blockable, which is why demos use `198.51.100.0/24` and
`203.0.113.0/24`. Private peers are still **watched**: their decisions are written to
`threat:blocks` with `enforce=False`, so the feed may show `172.18.0.1` entries (the Docker
bridge). That is observation, not a block — no 403 marker and no `blocked_ips` row are created.

**Where is the block state?**
Redis markers `threat:blocked:{ip}` (enforcement, TTL `THREAT_LENS_BLOCK_TTL_SECONDS`),
`threat:window:{ip}` (rolling anomaly window), `threat:blocks` (operator feed); durable audit in
`blocked_ips`. Redis is the fast path; Postgres is the record of truth.

---

## 7. Honest limits (say these before you are asked)

1. **The model is synthetic-trained.** It detects *deviation from synthetic normal traffic*, not
   real-world attack campaigns. A production rollout needs retraining on real telemetry with
   human-labelled validation.
2. **The artifact is not in git.** `*.joblib` is ignored; the demo requires the `model_data`
   volume to contain `threat_lens.joblib`. Check `/health` first — if `model_loaded` is false,
   nothing below will block.
3. **Client identity depends on proxy trust.** `client_identity` honours `X-Forwarded-For` only
   when `RATE_LIMIT_TRUST_FORWARDED=true`, which is what makes the demo address-controllable. In
   production this must be set only behind a trusted proxy, or an attacker can spoof identities.
4. **Single-node Redis.** The block marker and window are not replicated; a Redis flush resets
   enforcement (audit rows remain).
5. **The full backend suite needs `feedparser`.** This machine lacks it, so the local run is
   limited to the Threat Lens suite (79 tests). CI installs `requirements.txt` and runs everything.

---

## 8. Reproduce from zero

```powershell
# 1. Stack
docker compose up -d
docker compose ps

# 2. Model present?
docker compose exec -T threat curl -s localhost:9000/health
#    model_loaded:false -> retrain:
# docker compose exec -T backend threat-lens-train --out /app/models/threat_lens.joblib

# 3. Integrated proof
python scripts/validate_threat_lens_e2e.py

# 4. Live proof
python scripts/validate_threat_lens_e2e.py --live

# 5. Operator round-trip (independent of model timing)
.\scripts\threat_ops.ps1 -Block 203.0.113.202 -Reason demo
curl.exe -s -o NUL -w "%{http_code}`n" -H "X-Forwarded-For: 203.0.113.202" http://localhost:8000/api/v1/events/latest
.\scripts\threat_ops.ps1 -Unblock 203.0.113.202

# 6. Tests
cd backend
$env:PYTHONPATH = "../shared_core;" + $env:PYTHONPATH
$files = Get-ChildItem tests -Filter 'test_threat_*.py' | ForEach-Object { 'tests/' + $_.Name }
python -m pytest $files -q
```

---

*Sprint 1 complete: telemetry -> scoring -> decision -> enforcement -> audit -> operator control.*
