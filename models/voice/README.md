# models/voice — "off" 키워드 인식 (KWS)

> **상태: 모델 없음.** 데이터 수집·학습 전.

| 항목 | 계획 |
|---|---|
| 플랫폼 | Edge Impulse (Audio, MFCC/MFE + 1D CNN) |
| 클래스 | `off`, `noise`, `unknown` |
| 입력 | 16 kHz mono, 1 s window (XIAO 내장 PDM 마이크) |
| 배포 | Arduino library (int8) → `firmware/xiao-esp32s3-sense/libraries/` |
| 이벤트 매핑 | `model.task = "keyword-spotting"`, `data.keyword = "off"`, `data.confidence` |

## 이 디렉터리에 둘 것

- `MODEL_CARD.md` — 버전, 데이터셋 규모, 정확도, 오인식률(FAR)/미인식률(FRR), 임계값 근거
- 녹음 원본은 커밋하지 않는다 (`dataset/` 는 `.gitignore`).
