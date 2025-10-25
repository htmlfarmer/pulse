#!/usr/bin/env bash
# Generate a cryptographically strong secret for TRIGGER_SECRET
set -euo pipefail
if command -v openssl >/dev/null 2>&1; then
  openssl rand -hex 32
else
  python3 - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
fi
