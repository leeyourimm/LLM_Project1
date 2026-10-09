#!/usr/bin/env bash
# 백업: Postgres(pg_dump custom 형식) + Qdrant 컬렉션 스냅샷
#
#   infra/prod/backup.sh
#
# - 백업은 BACKUP_DIR(기본 infra/prod/backups)/<날짜-시각>/ 에 쌓인다.
# - 30일이 지난 백업은 지운다. 개인정보 처리방침에 "백업 사본은 최대 30일 뒤 사라진다"고
#   약속했으므로 보관 기간을 늘리지 않는다.
# - BACKUP_S3_BUCKET(예: my-bucket/dartrag)을 넣으면 aws cli 로 S3 에도 올리고,
#   S3 에서도 30일이 지난 것을 지운다. 지울 권한이 없으면(Terraform 으로 만든 서버는 일부러
#   삭제 권한을 주지 않는다) 경고만 남기고, 버킷의 수명 주기 규칙이 30일 안에 지운다.
# - OpenSearch 색인은 Postgres 에서 다시 만들 수 있어(dartrag index) 백업하지 않는다.
# 매일 새벽에 돌리려면 docs/deploy.md 의 cron 설정을 보세요.
set -euo pipefail

RETENTION_DAYS=30
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$HERE/.env"

# .env 에서 필요한 값만 읽는다 (source 하면 공백이 든 값에서 깨진다)
env_value() {
  local v
  v="$(grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -n1 | cut -d= -f2-)" || true
  v="${v%\"}"; v="${v#\"}"; v="${v%\'}"; v="${v#\'}"
  printf '%s' "$v"
}

BACKUP_DIR="${BACKUP_DIR:-$(env_value BACKUP_DIR)}"
BACKUP_DIR="${BACKUP_DIR:-$HERE/backups}"
BACKUP_S3_BUCKET="${BACKUP_S3_BUCKET:-$(env_value BACKUP_S3_BUCKET)}"

compose() {
  docker compose -f "$HERE/docker-compose.yml" "$@"
}

stamp="$(date +%Y%m%d-%H%M%S)"
dest="$BACKUP_DIR/$stamp"
mkdir -p "$dest"
chmod 700 "$BACKUP_DIR" "$dest"
echo "[$(date '+%F %T')] 백업 시작: $dest"

# 1) Postgres. 실행 중인 DB 를 멈추지 않고 일관된 시점으로 덤프한다
compose exec -T postgres pg_dump -U dartrag -d dartrag -Fc > "$dest/postgres.dump.part"
mv "$dest/postgres.dump.part" "$dest/postgres.dump"
echo "postgres: $(du -h "$dest/postgres.dump" | cut -f1)"

# 2) Qdrant. api 이미지 안의 파이썬으로 HTTP API 를 불러 스냅샷을 받는다
compose run --rm --no-deps -T \
  --user "$(id -u):$(id -g)" \
  -v "$HERE/qdrant_snapshot.py:/scripts/qdrant_snapshot.py:ro" \
  -v "$dest:/backup" \
  api python /scripts/qdrant_snapshot.py backup /backup/qdrant

chmod -R go-rwx "$dest"

# 3) S3 업로드 (선택)
if [ -n "$BACKUP_S3_BUCKET" ]; then
  s3="s3://${BACKUP_S3_BUCKET%/}"
  aws s3 cp --recursive --only-show-errors "$dest" "$s3/$stamp/"
  echo "s3: $s3/$stamp/ 업로드"
  cutoff="$(date -d "-$RETENTION_DAYS days" +%Y%m%d)"
  aws s3 ls "$s3/" | awk '$1 == "PRE" {print $2}' | while read -r prefix; do
    day="${prefix%%-*}"
    if [[ "$day" =~ ^[0-9]{8}$ ]] && [ "$day" -le "$cutoff" ]; then
      if aws s3 rm --recursive --only-show-errors "$s3/$prefix"; then
        echo "s3: 오래된 백업 삭제 $prefix"
      else
        echo "s3: $prefix 를 지울 수 없음 (권한 없음). 버킷 수명 주기 규칙이 지워야 한다" >&2
      fi
    fi
  done
fi

# 4) 30일이 지난 로컬 백업 삭제 (날짜-시각 이름의 폴더만)
find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -name '[0-9]*-[0-9]*' \
  -mtime +$((RETENTION_DAYS - 1)) -print -exec rm -rf {} +

echo "[$(date '+%F %T')] 백업 끝"
