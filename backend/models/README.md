# Model artifacts

This directory holds **serialized, production model artifacts only** — not
training code and not notebooks.

Expected contents (add when a model is promoted to production):

| Artifact | Produced by (offline) | Loaded by (runtime) |
| --- | --- | --- |
| `embedding/` (Sentence-Transformer weights or ONNX export) | `src/data/` ETL / notebook, exported once | `src/services/embedding_service.py` |
| `clusterer.joblib` (scikit-learn fitted pipeline) | offline training | `src/services/clustering_service.py` |

## Contract

1. Artifacts are produced **offline** and committed (or mounted from object
   storage); no training happens inside the API process.
2. Each artifact is loaded **once at startup** by its service and cached for the
   process lifetime. Do not re-read from disk per request.
3. Files here are git-ignored if large; keep a checksum/version note next to
   each artifact so a deploy is reproducible.

> The current embedding and clustering services resolve their models at runtime
> through the Hugging Face cache. Moving them to explicit files here is tracked
> as a Sprint 2 task.
