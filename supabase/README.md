# supabase

Supabase 는 **외부 관리형 PostgreSQL** 로 사용한다. Docker 컨테이너로 띄우지 않는다.

- `migrations/` — 타임스탬프 prefix SQL 마이그레이션 (Supabase CLI 규칙)
- 설계 문서: [`docs/database.md`](../docs/database.md)
- 보안 설계: [`docs/security.md`](../docs/security.md)

> 현재 마이그레이션은 작성만 되었고, 실제 Supabase 프로젝트에는 **적용되지 않았다.**
