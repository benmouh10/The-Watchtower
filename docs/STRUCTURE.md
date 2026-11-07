# Backend structure

The backend follows the clean layered architecture required for Sprint 2. The
only change from the previous layout is the physical location of files (module
paths were rewritten mechanically); business logic was not modified.

```
backend/
├── src/
│   ├── api/                 # HTTP layer: routing, controllers, validation, middleware
│   │   ├── controllers/     # thin route handlers (was app/controllers)
│   │   ├── schemas/         # Pydantic request/response contracts
│   │   ├── middleware/      # request middleware
│   │   └── main.py          # FastAPI app assembly (was app/main.py)
│   ├── services/            # business logic, inference, providers (was app/services)
│   ├── core/                # config, security, logging, database engine (was app/core)
│   └── data/                # data layer + offline ETL/bootstrap
│       ├── entities/        # SQLAlchemy ORM models (was app/entities)
│       ├── repositories/    # persistence queries (was app/repositories)
│       ├── seed_data.py     # offline seeding
│       ├── create_admin.py  # admin bootstrap
│       ├── resynth_clustered.py
│       ├── run_llm_direct.py
│       └── fix_zero_coords.py
├── tests/                   # pytest unit + integration suites
├── models/                  # serialized model artifacts (no training code)
├── scripts/                 # ad-hoc developer scripts (not part of the service)
├── alembic/                 # migrations
├── Dockerfile
└── requirements.txt
```

## Rules enforced

- `src/api/` must not contain business logic: handlers validate input, call a
  service, and format the response.
- `src/services/` owns all business rules; it never imports FastAPI request
  objects.
- `src/core/` holds cross-cutting concerns only.
- `src/data/` holds persistence + offline ingestion/ETL. Nothing here is
  imported by request handlers except repositories/entities.
- `models/` contains serialized artifacts loaded once at startup.
- `scripts/` is for one-off developer utilities and is excluded from the
  production image.

## Running

```bash
# tests (from backend/)
python -m pytest

# service
uvicorn src.api.main:app --reload
```

The Docker image and `docker-compose.yml` mount `./backend/src` at `/app/src`
and run `uvicorn src.api.main:app`.
