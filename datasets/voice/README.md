# datasets/voice

사용자의 **실제 음성 녹음**이 저장되는 위치. voice-service 컨테이너의 `/data/voice` 에 bind mount 된다.

```
raw/off/        "off" 발화        ← ESP32 `o`
raw/unknown/    다른 단어/대화    ← ESP32 `u`
raw/noise/      배경 소음         ← ESP32 `n`
metadata.jsonl  녹음당 1줄 메타데이터 (서버가 append)
segments.csv    (선택) 수동 구간 지정/제외 — segments.example.csv 참고
prepared/training, prepared/testing   prepare_voice_dataset.py 출력 (Edge Impulse 업로드용)
prepared/review.html, manifest.csv, report.json
```

## 관리 방식

- **WAV, metadata.jsonl, prepared/ 출력, segments.csv 는 Git 에 커밋하지 않는다** (`.gitignore`). 개인 음성이므로 공개 저장소에 올리지 않는다.
  `scripts/check_structure.sh` 는 녹음 파일이 git 에 추적되면 CI 를 실패시킨다.
- 백업은 Git 대신 외부 저장소(개인 클라우드 드라이브, 외장 디스크)에 `datasets/voice/` 폴더째 복사한다.
  Edge Impulse 프로젝트에 업로드된 데이터도 사실상의 백업 역할을 한다.
- 잘못된 녹음은 파일을 지우면 된다. `metadata.jsonl` 의 해당 줄은 남지만 통계/전처리는 디스크 파일 기준이다.
- CI 테스트는 합성(사인파) 오디오를 임시 디렉터리에 만들어 사용하며 이 폴더에 쓰지 않는다. 합성 오디오는 학습 데이터가 아니다.

## 확인

녹음이 실제로 "off" 를 담고 있는지는 **사용자가 직접 들어서 확인해야 한다**. 방법: [docs/voice-dataset.md §6](../../docs/voice-dataset.md).

```bash
python scripts/check_wav.py datasets/voice/raw
python scripts/prepare_voice_dataset.py      # → prepared/review.html 에서 재생
```
