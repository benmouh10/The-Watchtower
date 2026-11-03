"""Celery task module for Threat Lens anomaly scoring.

Consumes telemetry from the dedicated "threat" Celery queue:
1. Rebuilds the TelemetryEvent from the wire dict.
2. Fills windowed request rate for the client identity.
3. Scores the canonical 15-feature vector with the shared_core model.
4. Keeps a short rolling window of anomaly flags per identity in Redis.
5. Blocks an identity that trips the threshold (>=2 anomalies in window)
   by setting threat:blocked:<ip> with TTL in Redis and persisting a
   blocked_ips row in the database.
"""
from app.worker.threat_tasks import (
    analyze_telemetry_task,
    analyze_task,
)

analyze = analyze_task

__all__ = ["analyze", "analyze_telemetry_task", "analyze_task"]
