# simulator-web/site/assets

| 파일 | 용도 |
|---|---|
| `person.png` | **사용자가 넣는 사람 이미지** (탑뷰, 누운 자세, 머리가 위, 투명 배경 PNG 권장, 세로 약 1:2 비율) |
| `person-placeholder.svg` | `person.png` 가 없을 때 쓰는 기본 실루엣 |

`person.png` 를 이 폴더에 넣고 브라우저를 새로고침하면 반영된다 (compose 가 `site/` 를 nginx 에 읽기 전용으로 마운트하므로 재빌드 불필요).
침대 영역(약 110 × 190)에 비율을 유지한 채 맞춰진다.
