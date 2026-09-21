#!/usr/bin/env bash
# Insert a couple of demo links into a file-backed DB via the CLI.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
export NIMBUS_DB="${NIMBUS_DB:-$root/nimbus.db}"
python "$root/cli/nimbusctl.py" shorten "https://example.com/one"
python "$root/cli/nimbusctl.py" shorten "https://example.com/two"
echo "[seed] created 2 links in $NIMBUS_DB"
