#!/usr/bin/env bash
# 백업에서 되돌리기: Postgres 와 Qdrant 를 backup.sh 로 만든 폴더 하나의 시점으로 되돌린다
#
#   infra/prod/restore.sh infra/prod/backups/20260101-040000
#
# 지금 데이터는 덮어써진다. 되돌리는 동안 api·worker·beat 를 멈췄다가 끝나면 다시 켠다.
# S3 에만 있는 백업은 먼저 내려받는다: aws s3 cp --recursive s3://버킷/날짜-시각/ 폴더/
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ $# -ne 1 ] || [ ! -f "$1/postgres.dump" ]; then
  echo "사용법: $0 <백업 폴더>   (폴더 안에 postgres.dump 가 있어야 합니다)" >&2
  exit 1
fi
src="$(cd "$1" && pwd)"

compose() {
  docker compose -f "$HERE/docker-compose.yml" "$@"
}

echo "이 백업으로 되돌립니다: $src"
echo "지금 서버의 Postgres·Qdrant 데이터는 덮어써집니다."
read -r -p "계속하려면 yes 를 입력하세요: " answer
if [ "$answer" != "yes" ]; then
  echo "취소했습니다."
  exit 1
fi

# 되돌리는 동안 새 데이터가 쓰이지 않게 앱을 멈춘다
compose stop api worker beat

# 1) Postgres: 백업에 있는 테이블을 지우고 다시 만든다
compose up -d --wait postgres
compose exec -T postgres pg_restore -U dartrag -d dartrag \
  --clean --if-exists --no-owner --single-transaction < "$src/postgres.dump"
echo "postgres: 복원 끝"

# 2) Qdrant: 컬렉션마다 스냅샷을 올려 덮어쓴다
if [ -d "$src/qdrant" ]; then
  compose up -d --wait qdrant
  compose run --rm --no-deps -T \
    --user "$(id -u):$(id -g)" \
    -v "$HERE/qdrant_snapshot.py:/scripts/qdrant_snapshot.py:ro" \
    -v "$src/qdrant:/backup:ro" \
    api python /scripts/qdrant_snapshot.py restore /backup
fi

compose up -d
echo "되돌리기 끝. 검색 색인(OpenSearch)은 docs/deploy.md 의 안내대로 다시 만드세요."
