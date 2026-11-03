# infra/mosquitto — MQTT Broker

| 항목 | 내용 |
|---|---|
| 이미지 | `eclipse-mosquitto:2.0.20` |
| 상태 | **실제 동작** (개발용 무인증 설정) |
| 포트 | 컨테이너 1883 → host `127.0.0.1:1883` |
| 헬스체크 | `$SYS/broker/version` 구독 1건 수신 여부 |

## 파일

| 파일 | 용도 |
|---|---|
| `mosquitto.conf` | 개발용 설정. `allow_anonymous true`. compose 기본 마운트 |
| `mosquitto.auth.conf.example` | 인증/ACL/TLS 설정 템플릿 (실제 디바이스 연결 시) |
| `acl.example` | 역할별 topic 권한 템플릿 |
| `passwd`, `acl`, `mosquitto.auth.conf` | 로컬 생성 파일. `.gitignore` 처리, 커밋 금지 |

## 설정 구분

- **개발(현재)**: 무인증 + host 포트 `127.0.0.1` 바인딩. 같은 PC 안의 컨테이너/도구만 접속 가능.
- **LAN 실기기 연결(향후)**: ESP32 는 다른 기기이므로 localhost 바인딩으로는 접속 불가.
  이때는 반드시 `mosquitto.auth.conf` + `passwd` + `acl` 을 사용하고, 바인딩을 LAN IP 로 제한한다.
- **인터넷 노출**: 권장하지 않음. 필요 시 TLS(8883) + 인증 + ACL 필수, 1883 무인증 포트 공개 금지.

## 수동 확인

```bash
docker compose up -d mqtt-broker
docker compose exec mqtt-broker mosquitto_sub -t 'bedlight/v1/#' -v      # 터미널 1
docker compose exec mqtt-broker mosquitto_pub -t 'bedlight/v1/devices/test-01/events' -m '{}'  # 터미널 2
```
