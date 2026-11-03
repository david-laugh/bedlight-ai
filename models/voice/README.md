# models/voice — "off" 키워드 인식 (KWS)

> **상태: 모델 없음.** 데이터 수집 도구는 준비됨, 학습 전.
> 아키텍처 v2: 이 모델은 ESP32 가 아니라 **중앙 voice-service** 에서 실행된다.

| 항목 | 계획 |
|---|---|
| 플랫폼 | Edge Impulse (Audio, MFCC/MFE + 1D CNN) |
| 클래스 | `off`, `unknown`, `noise` |
| 입력 | 16 kHz mono 16-bit, 1 s window |
| 데이터 | `datasets/voice/` (ESP32 내장 마이크로 녹음) → `scripts/prepare_voice_dataset.py` |
| 배포 | Edge Impulse **Linux (x86_64) `.eim`** → `models/voice/deploy/` → voice-service `/models` |
| 이벤트 매핑 | `VOICE_KEYWORD_DETECTED`, `source=voice-service`, `model.runtime="edge-impulse-linux"` |

## 이 디렉터리

- `deploy/` — `.eim` 배포 파일 위치 (git ignore). 컨테이너에 read-only 마운트
- `MODEL_CARD.md` (학습 후 작성) — Edge Impulse project/deployment 버전, 데이터 수량, 정확도, FAR/FRR, 임계값 근거

절차: [docs/voice-dataset.md](../../docs/voice-dataset.md) → [docs/voice-inference-plan.md](../../docs/voice-inference-plan.md)
