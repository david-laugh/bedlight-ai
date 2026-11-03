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
  services/voice-service
  datasets/voice/raw/off
  datasets/voice/raw/unknown
  datasets/voice/raw/noise
  datasets/voice/prepared/training
  datasets/voice/prepared/testing
  models/voice/deploy
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
  firmware/xiao-esp32s3-sense/bedlight/bedlight.ino
  firmware/xiao-esp32s3-sense/bedlight/wav_header.h
  firmware/xiao-esp32s3-sense/bedlight/config.example.h
  firmware/xiao-esp32s3-sense/bedlight/config_defaults.h
  firmware/xiao-esp32s3-sense/bedlight/state_manager.h
  firmware/xiao-esp32s3-sense/bedlight/vision_inference.cpp
  firmware/xiao-esp32s3-sense/tests/test_state_manager.cpp
  services/voice-service/Dockerfile
  services/voice-service/README.md
  services/voice-service/app/main.py
  contracts/v1/voice-recording-response.schema.json
  datasets/voice/README.md
  scripts/check_wav.py
  scripts/prepare_voice_dataset.py
  docs/voice-dataset.md
  docs/voice-inference-plan.md
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

# Secrets / personal data guards
if git ls-files --error-unmatch .env >/dev/null 2>&1; then
  echo "[sec ] FAIL    .env is tracked by git"; fail=1
fi
if git ls-files | grep -E '(^|/)config\.h$' >/dev/null; then
  echo "[sec ] FAIL    firmware config.h (Wi-Fi credentials) is tracked by git"; fail=1
fi
if git ls-files 'datasets/voice/raw/**/*.wav' 'datasets/voice/prepared/**/*.wav' | grep -q .; then
  echo "[sec ] FAIL    voice recordings are tracked by git"; fail=1
fi

if [[ $fail -ne 0 ]]; then echo "structure check: FAIL"; exit 1; fi
echo "structure check: PASS"
