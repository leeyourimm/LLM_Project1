#!/usr/bin/env bash
# 내 컴퓨터(개발 환경, infra/docker-compose.yml)의 데이터를 서버로 옮길 묶음을 만든다.
# 공시 수집·파싱·색인은 맥에서 하고, 결과만 작은 서버로 옮길 때 쓴다 (docs/deploy-oracle.md).
#
#   infra/prod/export_local.sh
#
# 결과는 infra/prod/backups/<날짜-시각>/ 에 backup.sh 와 같은 모양으로 생긴다
#   postgres.dump  Postgres 전체 (pg_dump custom 형식)
#   qdrant/        Qdrant 컬렉션 스냅샷
# 서버에서는 이 폴더를 올린 뒤 restore.sh 로 되돌리고, 키워드 색인만 다시 만든다.
# 개발 환경의 대화 기록·계정도 함께 들어가니, 남기고 싶지 않으면 옮기기 전에 지운다.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
QDRANT_URL="${QDRANT_URL:-http://127.0.0.1:6333}"

dest="$HERE/backups/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$dest"
chmod 700 "$HERE/backups" "$dest"
echo "내보내기 시작: $dest"

docker compose -f "$ROOT/infra/docker-compose.yml" exec -T postgres \
  pg_dump -U dartrag -d dartrag -Fc > "$dest/postgres.dump.part"
mv "$dest/postgres.dump.part" "$dest/postgres.dump"
echo "postgres: $(du -h "$dest/postgres.dump" | cut -f1)"

QDRANT_URL="$QDRANT_URL" python3 "$HERE/qdrant_snapshot.py" backup "$dest/qdrant"
echo "qdrant: $(du -sh "$dest/qdrant" | cut -f1)"

echo "끝. 이 폴더를 서버의 /opt/dartrag/infra/prod/backups/ 로 올리세요:"
echo "$dest"
