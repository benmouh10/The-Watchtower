# Threat Lens — System Architecture & Design Specification

Threat Lens is the real-time, ML-driven edge abuse and anomaly defense system for GlobeLens AI (The Watchtower). It protects public news intelligence endpoints from distributed brute-force, credential stuffing, scraping floods, traversal scans, and anomalous traffic patterns without adding latency to legitimate HTTP requests.

---

## 1. High-Level Architecture

The architecture decouples lightweight edge enforcement from asynchronous ML scoring. The API gateway never executes machine learning inference synchronously on the request loop.

```
                              [ Incoming HTTP Request ]
                                         │
                                         ▼
                          ┌─────────────────────────────┐
                          │    ThreatLensMiddleware     │
                          │   (FastAPI Gateway :8000)   │
                          └──────────────┬──────────────┘
                                         │
                 ┌───────────────────────┴───────────────────────┐
                 │ 1. O(1) Redis GET (threat:blocked:<ip>)       │ 2. Fire-and-forget apply_async
                 ▼                                               ▼
       [ Is IP Blocked? ]                              ┌──────────────────┐
      ┌────────┴────────┐                              │  "threat" Queue  │
 YES  │                 │ NO                           │  (Redis Broker)  │
      ▼                 ▼                              └─────────┬────────┘
┌───────────┐     ┌───────────┐                                  │
│  403 Drop │     │ Endpoint  │                                  ▼
│ Forbidden │     │  Handler  │                        ┌───────────────────┐
└───────────┘     └─────┬─────┘                        │   Threat Worker   │
                        │                              │ (Celery Autoscale)│
                        │ 3. On success (< 500)        └─────────┬─────────┘
                        ▼                                        │
                  [ Enqueue Telemetry ]                          │ 4. Extract 15 Features
                                                                 ▼
                                                       ┌───────────────────┐
                                                       │  IsolationForest  │
                                                       │ (Percentile Risk) │
                                                       └─────────┬─────────┘
                                                                 │
                                                                 ▼
                                                       [ Risk >= 0.90? ]
                                                        (Rolling Window)
                                                         │ (>= 2 hits)
                                                         ▼
                                                  ┌─────────────────────────────┐
                                                  │ 1. SET threat:blocked:<ip>  │
                                                  │ 2. LPUSH threat:blocks feed │
                                                  │ 3. INSERT blocked_ips (SQL) │
                                                  └─────────────────────────────┘
```

---

## 2. Core Architectural Pillars

### A. Zero Latency Overhead on Gateway
- The gateway (`ThreatLensMiddleware`) only performs an $O(1)$ in-memory Redis check on entry.
- Request telemetry is dispatched asynchronously to the `threat` Celery queue using `apply_async(ignore_result=True)`.
- If the client is not blocked, the request proceeds immediately to the route handler.

### B. Single Source of Truth (`shared_core`)
- To eliminate **train/serve skew**, all feature definitions, mathematical transformations, and telemetry schemas are maintained in an independent library package: `shared_core/`.
- Both the training harness (`shared_core.training`) and the production scoring worker (`app.worker.threat_tasks`) import the exact same `extract_feature_vector()` function and `FEATURE_NAMES` contract.

### C. Fail-Open by Design
- If Redis is down, the broker is unreachable, or the ML model artifact is missing, the system **fails open**.
- Traffic is never dropped due to an internal monitoring or telemetry fault. Abuse defense degrades silently to unblocked operation.

### D. Multi-Event Windowing (Anti-False-Positive)
- A single high-entropy or noisy request **never** bans a user.
- An identity must trip the risk threshold ($\text{risk} \ge 0.90$) at least **2 times within a rolling window of 5 requests** before a block marker is written.

### E. Private Peer Safety
- Loopback (`127.0.0.1`) and RFC 1918 private subnets (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`) are exempt from edge 403 enforcement by default (`THREAT_LENS_SKIP_PRIVATE_PEERS=true`).
- Prevents developers or automated health probes from locking out local dev environments.

---

## 3. Data Contract & Feature Engineering

### Telemetry Event Schema (`TelemetryEvent`)
Defined in `shared_core.telemetry.TelemetryEvent`:

| Field | Type | Description |
|---|---|---|
| `ip` | `str` | Resolved client IP (supports `X-Forwarded-For` under trusted proxy). |
| `method` | `str` | HTTP Method (`GET`, `POST`, `PUT`, `DELETE`, etc.). |
| `path` | `str` | Normalized request path. |
| `payload_bytes` | `int` | Size of request payload from `Content-Length`. |
| `header_count` | `int` | Total count of HTTP headers received. |
| `header_entropy`| `float` | Shannon entropy over concatenated header name:value bytes. |
| `user_agent_present` | `bool` | Whether the `User-Agent` header exists. |
| `user_agent_length` | `int` | Length of the `User-Agent` string. |
| `content_type_is_json` | `bool` | `True` if `Content-Type` starts with `application/json`. |
| `has_authorization` | `bool` | `True` if valid `Bearer <token>` authorization is present. |
| `has_forwarded` | `bool` | Signal detecting upstream proxy spoofing headers. |
| `path_depth` | `int` | Number of `/` path segments. |
| `is_api_path` | `bool` | Whether path starts with `/api/v1/`. |
| `time_of_day` | `float` | Normalized UTC seconds into day $[0.0, 1.0]$. |
| `endpoint_sensitivity` | `float` | Risk weighting (e.g., auth routes = $1.0$, admin = $0.9$). |
| `status` | `int` | Eventual HTTP response status code. |
| `request_rate` | `float` | Rolling 60s request frequency computed by worker. |

### The 15-Column Feature Contract (`FEATURE_NAMES`)
Defined in `shared_core.features.FEATURE_NAMES`:

1. `request_rate` — requests per 60s window (capped at 100,000).
2. `payload_bytes_log` — $\ln(1 + \text{payload\_bytes})$ (capped at 10 MB).
3. `header_count` — total headers (capped at 200).
4. `header_entropy` — Shannon entropy (bits, range 0.0 to 10.0).
5. `user_agent_present` — binary indicator ($1.0$ if present, $0.0$ if omitted).
6. `user_agent_length_log` — $\ln(1 + \text{len(UA)})$.
7. `content_type_is_json` — binary indicator ($1.0$ or $0.0$).
8. `has_authorization` — binary indicator ($1.0$ or $0.0$).
9. `has_forwarded` — binary indicator ($1.0$ or $0.0$).
10. `path_depth` — path segment depth (clipped between 1 and 12).
11. `is_api_path` — binary indicator ($1.0$ or $0.0$).
12. `time_of_day_sin` — $\sin(2\pi \times \text{time\_of\_day})$.
13. `time_of_day_cos` — $\cos(2\pi \times \text{time\_of\_day})$.
14. `endpoint_sensitivity` — float between $0.0$ and $1.0$.
15. `method_num` — categorical encoding (GET=0, POST=1, PUT=2, PATCH=3, DELETE=4, other=5).

---

## 4. Machine Learning & Anomaly Scoring

### Unsupervised Isolation Forest
- **Model:** `sklearn.ensemble.IsolationForest(n_estimators=200, contamination="auto")`.
- **Training Population:** Trained purely on **normal synthetic traffic** (`shared_core.training`).
- It learns the multi-dimensional manifold of legitimate user traffic; attacks are defined as data points that the forest easily isolates.

### Percentile Risk Calibration
Raw decision scores from `decision_function()` are negative for outliers and positive for inliers, but have no probabilistic meaning. Threat Lens calibrates the score into a percentile rank:

$$\text{Risk}(x) = \frac{\sum_{i=1}^{N_{\text{train}}} \mathbb{I}(s_{\text{train}}^{(i)} > s(x))}{N_{\text{train}}}$$

- $\text{Risk} \to 1.0$: Point is more isolated than almost all normal training samples.
- $\text{Risk} \to 0.0$: Point sits deep inside standard normal traffic patterns.
- An anomaly is flagged when $\text{Risk}(x) \ge \text{THREAT\_LENS\_RISK\_THRESHOLD}$ ($0.90$).

---

## 5. Dual-Layer State & Persistence

When an IP trips the anomaly threshold:
1. **Volatile Edge Marker (Redis):**  
   `SET threat:blocked:<ip> 1 EX 3600`  
   Fast, lookup-free, auto-expiring edge drop checked by the FastAPI middleware.
2. **Audit Feed (Redis):**  
   `LPUSH threat:blocks '{"ip": "...", "reason": "...", "risk": 0.94, "time": ...}'`  
   In-memory feed consumed by the real-time admin monitoring dashboard.
3. **Durable Storage (PostgreSQL):**  
   Upserted into the `blocked_ips` table (`id`, `ip`, `source`, `reason`, `risk_score`, `blocked_at`, `expires_at`). Survives Redis restarts and preserves historical audit trails.

---

## 6. Diagnostic Scorer Sidecar (`:9000`)

The `threat` container runs a dedicated sidecar service ([app/scoring_api.py](backend/app/scoring_api.py)) alongside the Celery worker:
- **`GET :9000/health`**: Reports `status: ok`, `model_loaded: true|false`, and model file path.
- **`POST :9000/score`**: Accepts any raw `TelemetryEvent` dict and returns:
  ```json
  {
    "identity": "198.51.100.42",
    "risk": 0.9542,
    "threshold": 0.90,
    "violates": true,
    "feature_names": ["request_rate", "payload_bytes_log", ...],
    "features": [12.0, 5.2, 8.0, ...]
  }
  ```
  Provides a read-only microscope to inspect why a specific request was flagged without mutating block state.

---

## 7. Operator Knobs (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `THREAT_LENS_ENABLED` | `false` | Master toggle for middleware sampling & edge blocking. |
| `THREAT_LENS_QUEUE` | `threat` | Dedicated Celery queue name on Redis. |
| `THREAT_LENS_MODEL_PATH` | `/app/models/threat_lens.joblib` | Model artifact path on shared volume. |
| `THREAT_LENS_RISK_THRESHOLD` | `0.90` | Risk percentile threshold for an anomaly hit. |
| `THREAT_LENS_WINDOW_MIN_ANOMALIES` | `2` | Number of anomaly hits required to block. |
| `THREAT_LENS_WINDOW_SIZE` | `5` | Rolling request window per identity. |
| `THREAT_LENS_BLOCK_TTL_SECONDS` | `3600` | Block duration (1 hour). |
| `THREAT_LENS_SKIP_PRIVATE_PEERS` | `true` | Never 403 loopback or RFC 1918 traffic. |
| `THREAT_SCORE_PORT` | `9000` | Sidecar diagnostic port. |
