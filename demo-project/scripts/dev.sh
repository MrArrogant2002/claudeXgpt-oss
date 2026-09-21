#!/usr/bin/env bash
# Start the Nimbus dev loop. In this demo it just describes the services; a real
# launcher would start the API server, the worker, and the web dev server.
set -euo pipefail
echo "[dev] Nimbus — services:"
echo "  api    (python)  services/api      -> POST /shorten, GET /<code>, GET /api/stats/<code>"
echo "  worker (go)      services/worker   -> aggregates click events"
echo "  web    (ts/js)   web               -> link form + stats panel"
echo "[dev] run ./scripts/run_tests.sh to execute the test suites"
