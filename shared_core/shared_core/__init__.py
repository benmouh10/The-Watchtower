"""The Watchtower Threat Lens shared core.

Versioned request telemetry, feature engineering, the unsupervised anomaly
model and the synthetic-traffic trainer. Imported by both the FastAPI
gateway (which emits telemetry) and the scoring worker (which consumes it
and owns the trained model), so the exact same code produces the features
during training and during serving.
"""