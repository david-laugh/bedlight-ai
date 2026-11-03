# services/simulator-web

> **상태: PLACEHOLDER** — nginx 가 정적 안내 페이지(`placeholder/index.html`)만 제공한다.
> 시뮬레이션·WebSocket·조명 제어 로직은 없다.

접속: <http://127.0.0.1:8080> (`SIMULATOR_HOST_PORT` 로 변경 가능)

## 역할 (향후 구현)

React + Vite 기반 가상 침실. 침대·사람(누움 상태)·조명을 표시하고, Controller 로부터 받은
`LIGHT_OFF_REQUESTED` 를 화면에 적용한 뒤 `LIGHT_OFF_APPLIED` 결과를 반환한다.

## Input / Output 계약

| 방향 | 채널 | 페이로드 |
|---|---|---|
| IN  | WebSocket client → `ws://<controller>/ws/simulator/{room_id}` | `LIGHT_OFF_REQUESTED` envelope, (선택) `LYING_*`/`VOICE_*` 상태 표시용 이벤트 |
| OUT | 같은 WebSocket | `LIGHT_OFF_APPLIED` envelope, `source="simulator"`, 원 요청의 `trace_id` 유지, `data.request_event_id` 에 요청 `event_id` |
| OUT | (선택) WebSocket | `SYSTEM_ERROR` (렌더 실패 등) |

`timing.simulator_received_at`, `timing.simulator_applied_at`, `timing.render_ms` 를 채운다.
`render_ms` 는 상태 변경 후 `requestAnimationFrame` 2회 콜백까지의 시간으로 측정한다(docs/performance-plan.md).

## 향후 디렉터리 구조 (예정)

```
services/simulator-web/
  package.json, vite.config.(js|ts), index.html
  src/App.(jsx|tsx)
  src/scene/Bedroom.*        # 침실/사람/조명
  src/net/controllerSocket.* # WebSocket, 재연결(backoff)
  src/contracts/             # 이벤트 타입
  placeholder/               # React 도입 후 삭제
```

`package.json` 이 생기면 `services-ci.yml` 의 frontend job 이 자동 활성화된다 (`npm ci`, `npm run build`, `npm test --if-present`).
