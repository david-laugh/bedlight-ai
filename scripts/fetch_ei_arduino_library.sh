#!/usr/bin/env bash
# Download the Edge Impulse Arduino library (int8, TFLite) of a project via the Edge Impulse API.
#   EI_API_KEY=ei_... EI_PROJECT_ID=1155470 scripts/fetch_ei_arduino_library.sh <out.zip>
# The library (~176 MB unpacked) is not committed to git; CI fetches it with the EI_API_KEY secret.
# API endpoints used (Edge Impulse Studio REST API v1):
#   POST /v1/api/{project}/jobs/build-ondevice-model?type=arduino   -> {success, id}
#   GET  /v1/api/{project}/jobs/{id}/status                          -> {job:{finished, finishedSuccessful}}
#   GET  /v1/api/{project}/deployment/download?type=arduino&modelType=int8
set -euo pipefail
: "${EI_API_KEY:?EI_API_KEY is required}"
: "${EI_PROJECT_ID:?EI_PROJECT_ID is required}"
OUT="${1:-ei-arduino-library.zip}"
API="https://studio.edgeimpulse.com/v1/api/${EI_PROJECT_ID}"
H=(-H "x-api-key: ${EI_API_KEY}" -H "Content-Type: application/json")

echo "Starting Arduino library build for project ${EI_PROJECT_ID}"
resp=$(curl -fsS -X POST "${H[@]}" "${API}/jobs/build-ondevice-model?type=arduino" \
  -d '{"engine":"tflite","modelType":"int8"}')
job=$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); assert d.get("success"), d; print(d["id"])' "$resp")
echo "job id ${job}"

for _ in $(seq 1 120); do
  st=$(curl -fsS "${H[@]}" "${API}/jobs/${job}/status")
  done=$(python3 -c 'import json,sys; j=json.loads(sys.argv[1])["job"]; print("1" if j.get("finished") else "0", "1" if j.get("finishedSuccessful") else "0")' "$st")
  if [[ "${done%% *}" == "1" ]]; then
    [[ "${done##* }" == "1" ]] || { echo "Edge Impulse build job failed"; exit 1; }
    break
  fi
  sleep 5
done

curl -fsS -L "${H[@]}" -o "$OUT" "${API}/deployment/download?type=arduino&modelType=int8&engine=tflite"
unzip -l "$OUT" | grep -q "inferencing.h" || { echo "downloaded zip does not look like an Edge Impulse Arduino library"; exit 1; }
echo "saved $OUT ($(du -h "$OUT" | cut -f1))"
