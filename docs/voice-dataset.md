# 음성 데이터셋 수집 가이드 (ESP32 → Voice Service → Edge Impulse)

목표: XIAO ESP32S3 Sense 에 "off" 라고 말한 **실제 음성**을 PC 의 `datasets/voice/raw/off/` 에 WAV 로 저장하고,
Edge Impulse 학습용으로 정리한다.

> 상태: 펌웨어 컴파일(Core 2.0.17)과 서버 수신·저장은 검증됨. **실제 보드에서의 녹음·업로드는 아직 검증되지 않았다** (아래 H1~H6 체크리스트로 확인).

---

## 1. PC: Voice Service 실행

```bash
cp .env.example .env          # (선택) VOICE_UPLOAD_TOKEN 설정
docker compose up -d --build voice-service
curl http://127.0.0.1:8001/health
# {"status":"ok","service":"voice-service",...,"data_dir_writable":true,"model_loaded":false}
```

PC 의 LAN IP 확인 (ESP32 설정에 사용):

- Windows: `ipconfig` → Wi-Fi/이더넷 어댑터의 **IPv4 주소** (예: `192.168.0.23`)
- macOS/Linux: `ipconfig getifaddr en0` / `hostname -I`

같은 LAN 의 다른 기기(휴대폰 브라우저 등)에서 `http://<PC-IP>:8001/health` 가 열리는지 먼저 확인한다.
열리지 않으면 **Windows 방화벽** 인바운드 규칙에서 TCP 8001 (개인 네트워크)을 허용한다:

```powershell
# 관리자 PowerShell
New-NetFirewallRule -DisplayName "BedLight voice-service 8001" -Direction Inbound -Protocol TCP -LocalPort 8001 -Profile Private -Action Allow
```

## 2. ESP32 펌웨어 설정

```
firmware/xiao-esp32s3-sense/bedlight/
  bedlight.ino          # DATASET_RECORDING 펌웨어
  wav_header.h          # WAV 헤더 / PCM 처리 (호스트 단위 테스트 대상)
  config.example.h      # 설정 템플릿 (커밋됨)
  config.h              # 실제 설정 (직접 생성, .gitignore — 커밋 금지)
```

1. `config.example.h` 를 같은 폴더에 `config.h` 로 복사
2. 값 수정:

| 항목 | 값 |
|---|---|
| `WIFI_SSID`, `WIFI_PASSWORD` | 2.4 GHz Wi-Fi (ESP32-S3 는 5 GHz 미지원) |
| `VOICE_SERVER_HOST` | **PC 의 LAN IP** (예: `"192.168.0.23"`). `localhost`, `127.0.0.1`, `voice-service` 사용 금지 — 부팅 시 `[ERROR]` 출력 |
| `VOICE_SERVER_PORT` | `8001` |
| `VOICE_UPLOAD_TOKEN` | `.env` 의 `VOICE_UPLOAD_TOKEN` 과 동일 (비워 두면 토큰 미사용) |
| `DEVICE_ID` | `bedlight-01` |
| `AUDIO_GAIN` | 기본 4. 너무 작으면 올리고, `[WARN] clipping` 이 나오면 낮춘다 |

## 3. Arduino IDE 업로드

1. Arduino IDE → Preferences → Additional boards manager URLs:
   `https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json`
2. Boards Manager → **esp32 by Espressif Systems 2.0.17** 설치 (3.x 사용 금지)
3. `firmware/xiao-esp32s3-sense/bedlight/bedlight.ino` 열기
4. Tools 설정:

| 항목 | 값 |
|---|---|
| Board | XIAO_ESP32S3 |
| PSRAM | **OPI PSRAM** |
| USB CDC On Boot | **Enabled** |
| Partition Scheme | Default with spiffs (기본값) |

5. Upload → Serial Monitor **115200 baud** 열기 (줄 끝 설정 무관)

CLI 동등 명령: `arduino-cli compile --fqbn esp32:esp32:XIAO_ESP32S3:PSRAM=opi,CDCOnBoot=default firmware/xiao-esp32s3-sense/bedlight`

## 4. 녹음

Serial Monitor 에 한 글자 입력 후 전송:

| 명령 | 동작 |
|---|---|
| `o` | "off" 녹음 |
| `u` | unknown (다른 단어·일상 대화) 녹음 |
| `n` | noise (말하지 않음, 배경 소음) 녹음 |
| `s` | 마이크·PSRAM·Wi-Fi·서버 주소·카운터 상태 |
| `h` | 도움말 |

`o` 입력 후 흐름: `Recording OFF sample...` → 약 1 초 대기 → **`>>> SPEAK NOW <<<` 표시 직후 "off" 발화** → 2 초 녹음 → 업로드 → 결과.

정상일 때 예상 형식 (예시이며 실측 로그가 아님):

```
Recording OFF sample...
Recording label: off
Sample rate: 16000Hz
Channels: 1
Bits per sample: 16
Duration: 2000ms
>>> SPEAK NOW <<<
Recorded 32000 samples in 2001 ms (dc_offset=..., peak=..., clipped=0)
Uploading audio...
Upload successful
Saved: off_20261103T153012_3f6c1a2e.wav
Server status: saved, validation: valid
[metrics] capture_id=... label=off record_ms=... wav_bytes=64044 upload_ms=... http=201 free_heap=... free_psram=...
```

실패는 항상 `[FAIL]` / `[ERROR]` 로 시작한다 (I2S 초기화, PSRAM 할당, 샘플 수 불일치, Wi-Fi, HTTP).
업로드 실패 시 녹음은 폐기된다 (SD 카드 미사용). 다시 녹음하면 된다.

### 녹음 요령 (권장, 확정 기준 아님)

- 클래스별 최소 수십 개, 가능하면 100 개 이상. `off` 와 `unknown`/`noise` 수량 균형 유지
- 거리(30 cm / 1 m / 2 m), 목소리 크기, 말 속도, 방향을 다양하게
- 실제 사용 환경(침대에 누운 위치)에서 녹음
- `unknown`: "on", "of", "오프" 이외의 단어, 비슷한 발음("cough", "soft", "office"), 일상 대화
- `noise`: 무음, 선풍기·에어컨, TV, 이불 소리, 키보드 등

## 5. 저장 위치 (PC)

```
datasets/voice/
  raw/off/off_<YYYYMMDDTHHMMSS>_<capture8>.wav     # 서버 수신 시각(UTC) + capture_id 앞 8자
  raw/unknown/...
  raw/noise/...
  metadata.jsonl                                   # 녹음당 1줄
  prepared/training/, prepared/testing/            # prepare 스크립트 출력
```

`metadata.jsonl` 필드: `capture_id, filename, relative_path, label, device_id, sample_rate, channels, bits_per_sample,
num_samples, duration_ms, file_size, captured_at, validation_status, warnings, quality{...}, device{record_ms, free_heap, free_psram, ...}`.
`captured_at` 은 **서버 수신 시각(UTC)** 이다 (ESP32 는 RTC/NTP 시각 없음).

서버 통계: `curl http://127.0.0.1:8001/api/v1/recordings/stats`

## 6. 검증 — 반드시 직접 들어볼 것

```bash
python scripts/check_wav.py datasets/voice/raw            # 표 출력
python scripts/check_wav.py datasets/voice/raw --strict   # 형식 불일치 시 exit 1
```

출력: Sample Rate, Channels, Bits, Duration, Samples, RMS dBFS, Peak, Clipping, 무음 참고(`likely`/`all-zero`).
**이 지표는 "off" 를 실제로 말했는지 판단하지 못한다.** 다음 방법으로 직접 재생해 확인한다.

| 방법 | 명령 |
|---|---|
| Windows 탐색기 | `datasets\voice\raw\off` 폴더에서 더블클릭 (미디어 플레이어) |
| 폴더 전체 연속 재생 | VLC: `vlc datasets/voice/raw/off` |
| ffmpeg | `ffplay -autoexit datasets/voice/raw/off/<file>.wav` |
| 파형/스펙트로그램 | Audacity 에 여러 파일 드래그 (트랙별 파형 비교) |
| 브라우저 | `prepare_voice_dataset.py` 가 만든 `datasets/voice/prepared/review.html` (원본 + 선택 구간 재생, 파형 표시) |

잘못 녹음된 파일(말이 잘림, 기침, 다른 단어)은 **파일을 삭제**하면 된다. `/stats` 와 prepare 스크립트는 디스크에 있는 파일 기준으로 동작한다.

## 7. Edge Impulse 학습용 정리

```bash
python scripts/prepare_voice_dataset.py --dry-run     # 집계만
python scripts/prepare_voice_dataset.py               # prepared/ 생성
```

- 1 초 윈도우 생성: `off`/`unknown` 은 에너지가 가장 큰 1 초(발화 추정), `noise` 는 겹치지 않는 1 초 단위 분할
- 자동 선택은 추정일 뿐이다. `prepared/review.html` 에서 **모든 클립을 들어보고** 잘못된 것은
  `datasets/voice/segments.csv` 로 수정 (`segments.example.csv` 참고):
  - `include,<start_ms>` : 구간 시작 수동 지정
  - `exclude` : 해당 녹음 제외
- Training/Testing 분리: **녹음 파일(PCM 내용 해시) 단위**. 같은 녹음에서 나온 구간이나 바이트가 같은 복사본은
  절대 두 split 에 동시에 들어가지 않는다. 파일을 추가해도 기존 파일의 split 은 바뀌지 않는다 (해시 기반, 기본 20 % testing)
- 플래그: `low_energy_review` (발화 없음 의심), `energy_at_window_border` (단어 잘림 의심), `window_at_recording_edge`
- 출력: `prepared/{training,testing}/<label>.<원본>.s<start_ms>.wav`, `manifest.csv`, `report.json`, `review.html`

## 8. Edge Impulse 업로드

파일명이 `<label>.` 로 시작하므로 Edge Impulse 가 라벨을 자동 추론한다.

**Studio (웹)**: Data acquisition → Upload data →
`prepared/training` 의 파일 선택, Upload into category **Training**, Label **Infer from filename** →
`prepared/testing` 도 같은 방식으로 **Testing** 카테고리에 업로드. (자동 split 옵션은 사용하지 않는다 — 누수 방지 split 을 이미 했으므로)

**CLI** (Node.js 필요, Git Bash 등 glob 확장되는 셸에서):

```bash
npm install -g edge-impulse-cli
edge-impulse-uploader --category training datasets/voice/prepared/training/*.wav
edge-impulse-uploader --category testing  datasets/voice/prepared/testing/*.wav
```

학습 설정 제안 (Edge Impulse KWS 표준 구성): Time series 1000 ms window, 16 kHz → MFCC 또는 MFE → Classification (1D CNN).
배포는 **Linux (x86_64) .eim** 으로 → [voice-inference-plan.md](voice-inference-plan.md).

## 9. 수동 하드웨어 통합 테스트 (CI 로 검증 불가)

| ID | 항목 | 확인 방법 | 결과 |
|---|---|---|---|
| H1 | 부팅·초기화 | 시리얼에 `I2S PDM microphone initialized`, `Wi-Fi connected`, `[ERROR]` 없음 | 미검증 |
| H2 | 상태 명령 | `s` → PSRAM found, I2S READY, 서버 주소 = PC LAN IP | 미검증 |
| H3 | 녹음 길이 | `o` → `Recorded 32000 samples in ~2000 ms` (±10 % 이내, 아니면 `[WARN]`) | 미검증 |
| H4 | 업로드 | `Upload successful`, `Saved: off_....wav`, PC `datasets/voice/raw/off/` 에 파일 생성 | 미검증 |
| H5 | 실제 음성 | 파일을 재생해 본인의 "off" 가 들리고, `check_wav.py` 결과 형식 OK·무음 아님·클리핑 없음 | 미검증 |
| H6 | 연속 녹음 | `o` 를 20 회 연속 → 모두 성공, `s` 의 PSRAM free 값이 감소하지 않음(누수 없음) | 미검증 |
| H7 | 실패 처리 | 서버 중지 후 `o` → `[FAIL] Upload failed`, 이후 서버 재시작 → 다시 성공 | 미검증 |
