# tests/performance

> **상태: 측정 도구 없음, 측정 결과 없음.**

계획: [`docs/performance-plan.md`](../../docs/performance-plan.md)

예정 구성:

```
tests/performance/
  run_load.py          # mock-device 를 1/5/20 evt/s 로 구동
  collect_stats.sh     # docker stats --no-stream 주기 수집 → CSV
  analyze.py           # trace_id 기준 구간 지연 계산, 평균/P95/실패율/유실률
  results/             # 측정 결과 (.gitignore, 요약만 docs 에 기록)
```
