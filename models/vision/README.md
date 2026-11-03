# models/vision — 누움 상태 이미지 분류

> **상태: 모델 없음.** 데이터 수집·학습 전.

| 항목 | 계획 |
|---|---|
| 플랫폼 | Edge Impulse (Image classification, Transfer learning MobileNetV2 계열 후보) |
| 클래스 | `lying`, `not_lying` (필요 시 `empty_bed`) |
| 입력 | 96×96 grayscale 또는 RGB (XIAO PSRAM/추론시간 측정 후 결정) |
| 배포 | Arduino library (int8 quantized) → `firmware/xiao-esp32s3-sense/libraries/` |
| 이벤트 매핑 | `model.task = "vision-classification"`, `data.confidence`, `timing.inference_ms` |

## 이 디렉터리에 둘 것

- `MODEL_CARD.md` — 모델 버전, 데이터셋 규모, 정확도/혼동행렬, 임계값 결정 근거, Edge Impulse project/deployment 번호
- 데이터셋 원본은 커밋하지 않는다 (`dataset/` 는 `.gitignore`). 개인 영상이 포함될 수 있으므로 공유 범위에 주의.
