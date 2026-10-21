#!/bin/sh
# Threat Lens container entry point: runs the dedicated scoring worker on the
# "threat" queue plus the internal /score debug API on port 9000.
#
# The scoring worker uses --autoscale so it ramps from one thread up to four
# only when the threat queue is busy — the lightweight equivalent of a KEDA
# HorizontalPodAutoscaler, per the S5 architecture (see the innovation doc).
# The uvicorn debug API is a sidecar; the container's life is the worker's.
set -eu

echo "[threat] starting scoring debug API on :9000"
uvicorn app.scoring_api:app --host 0.0.0.0 --port 9000 --no-access-log &

echo "[threat] starting threat worker on queue 'threat' (autoscale 4,1)"
exec celery -A app.worker.celery_app worker \
    -Q "${THREAT_LENS_QUEUE:-threat}" \
    -n "threat@%h" \
    --autoscale=4,1 \
    --loglevel=INFO