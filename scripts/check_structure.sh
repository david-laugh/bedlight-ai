#!/usr/bin/env bash
# Verify that the BedLight AI repository skeleton is intact.
set -euo pipefail
cd "$(dirname "$0")/.."

required_dirs=(
  firmware/xiao-esp32s3-sense
  models/vision
  models/voice
  services/controller
  services/db-writer
  services/simulator-web
  infra/mosquitto
  tests/mock-device
  tests/integration
  tests/performance
  contracts/v1
  contracts/v1/examples/valid
  contracts/v1/examples/invalid
  supabase/migrations
  docs
  .github/workflows
)

required_files=(
  README.md
  compose.yaml
  .env.example
  .gitignore
  infra/mosquitto/mosquitto.conf
  services/controller/Dockerfile
  services/controller/README.md
  services/db-writer/Dockerfile
  services/db-writer/README.md
  services/simulator-web/Dockerfile
  services/simulator-web/README.md
  tests/mock-device/Dockerfile
  tests/mock-device/README.md
  contracts/v1/event-envelope.schema.json
  contracts/v1/device-status.schema.json
  contracts/v1/topics.json
  firmware/xiao-esp32s3-sense/README.md
  firmware/xiao-esp32s3-sense/toolchain.env
  docs/architecture.md
  docs/interfaces.md
  docs/database.md
  docs/security.md
  docs/test-plan.md
  docs/performance-plan.md
  .github/workflows/scaffold-ci.yml
  .github/workflows/firmware-ci.yml
  .github/workflows/services-ci.yml
)

fail=0
for d in "${required_dirs[@]}"; do
  if [[ -d "$d" ]]; then echo "[dir ] OK      $d"; else echo "[dir ] MISSING $d"; fail=1; fi
done
for f in "${required_files[@]}"; do
  if [[ -f "$f" ]]; then echo "[file] OK      $f"; else echo "[file] MISSING $f"; fail=1; fi
done

if ls supabase/migrations/*.sql >/dev/null 2>&1; then
  echo "[file] OK      supabase/migrations/*.sql"
else
  echo "[file] MISSING supabase/migrations/*.sql"; fail=1
fi

# Secrets guard: a real .env must never be committed.
if git ls-files --error-unmatch .env >/dev/null 2>&1; then
  echo "[sec ] FAIL    .env is tracked by git"; fail=1
fi

if [[ $fail -ne 0 ]]; then echo "structure check: FAIL"; exit 1; fi
echo "structure check: PASS"
