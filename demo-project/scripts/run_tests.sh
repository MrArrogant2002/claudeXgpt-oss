#!/usr/bin/env bash
# Run every test suite we can on this machine. Missing toolchains are skipped, so
# this works whether or not go/node are installed. Exit non-zero if anything fails.
set -uo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
fail=0

echo "== python api =="
( cd "$root/services/api" && python -m unittest discover -s tests -p "test_*.py" ) || fail=1

echo "== web (node) =="
if command -v node >/dev/null 2>&1; then
  node "$root/web/test/format.test.js" || fail=1
else
  echo "  (node not installed — skipped)"
fi

echo "== worker (go) =="
if command -v go >/dev/null 2>&1; then
  ( cd "$root/services/worker" && go test ./... ) || fail=1
else
  echo "  (go not installed — skipped)"
fi

if [ "$fail" -ne 0 ]; then
  echo "SOME TESTS FAILED"
else
  echo "ALL AVAILABLE SUITES PASSED"
fi
exit $fail
