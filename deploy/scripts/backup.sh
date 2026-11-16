#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this backup as root through otternest-backup.service." >&2
  exit 1
fi

BACKUP_DIR="${OTTERNEST_BACKUP_DIR:-/var/backups/otternest}"
RETENTION_DAYS="${OTTERNEST_BACKUP_RETENTION_DAYS:-14}"
ARTIFACT_DIR="${OTTERNEST_ARTIFACT_DIR:-/var/lib/otternest/artifacts}"
APP_DIR="/opt/otternest/app"
SERVICE_USER="otternest"

if [[ ! "${RETENTION_DAYS}" =~ ^[1-9][0-9]{0,2}$ ]] || (( RETENTION_DAYS > 365 )); then
  echo "Backup retention must be an integer between 1 and 365 days." >&2
  exit 1
fi
if [[ ! -d "${BACKUP_DIR}" || -L "${BACKUP_DIR}" ]]; then
  echo "Backup directory must exist and must not be a symlink: ${BACKUP_DIR}" >&2
  exit 1
fi
if [[ ! -d "${ARTIFACT_DIR}" || -L "${ARTIFACT_DIR}" ]]; then
  echo "Artifact directory must exist and must not be a symlink: ${ARTIFACT_DIR}" >&2
  exit 1
fi
if [[ ! -d "${APP_DIR}/.git" ]]; then
  echo "OtterNest Git checkout not found at ${APP_DIR}" >&2
  exit 1
fi

TMP_DIR="$(mktemp -d "${BACKUP_DIR}/.otternest-backup.XXXXXX")"
declare -A WAS_ACTIVE=()
RESTORE_ORDER=(
  otternest-api.service
  otternest-scheduler.service
  otternest-workflows.service
  otternest-webhooks.service
)

restore_services() {
  local status=$?
  trap - EXIT
  for service in "${RESTORE_ORDER[@]}"; do
    if [[ "${WAS_ACTIVE[$service]:-0}" == "1" ]]; then
      if ! systemctl start "$service"; then
        echo "WARNING: failed to restart previously active service $service" >&2
        status=1
      fi
    fi
  done
  rm -rf -- "$TMP_DIR"
  exit "$status"
}
trap restore_services EXIT

# Remember the initial service state so a failed backup does not change it.
for service in "${RESTORE_ORDER[@]}"; do
  if systemctl is-active --quiet "$service"; then
    WAS_ACTIVE["$service"]=1
  else
    WAS_ACTIVE["$service"]=0
  fi
done

# Stop event delivery and new task routing before stopping the API.
systemctl stop otternest-webhooks.service
systemctl stop otternest-workflows.service
systemctl stop otternest-scheduler.service
systemctl stop otternest-api.service

# Refuse a backup while any task is still marked running. The API is stopped,
# so no new Runner claim can race this check.
running_tasks="$(runuser -u "$SERVICE_USER" -- psql --no-psqlrc --tuples-only --no-align \
  --dbname=otternest --command="SELECT count(*) FROM tasks WHERE status = 'running';")"
running_tasks="${running_tasks//[[:space:]]/}"
if [[ ! "${running_tasks}" =~ ^[0-9]+$ ]]; then
  echo "Could not verify active task count; refusing backup." >&2
  exit 1
fi
if (( running_tasks > 0 )); then
  echo "Refusing backup: ${running_tasks} task(s) are still running." >&2
  exit 1
fi

# PostgreSQL peer authentication requires pg_dump to run as the otternest OS user.
runuser -u "$SERVICE_USER" -- pg_dump --format=custom --no-owner --no-acl \
  --dbname=otternest | cat > "${TMP_DIR}/database.dump"

tar -czf "${TMP_DIR}/artifacts.tar.gz" -C "$(dirname -- "$ARTIFACT_DIR")" \
  "$(basename -- "$ARTIFACT_DIR")"

created_utc="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
git_commit="$(runuser -u "$SERVICE_USER" -- git -C "$APP_DIR" rev-parse HEAD)"
alembic_revision="$(runuser -u "$SERVICE_USER" -- psql --no-psqlrc --tuples-only --no-align \
  --dbname=otternest --command="SELECT version_num FROM alembic_version LIMIT 1;")"
alembic_revision="${alembic_revision//[[:space:]]/}"

{
  printf 'created_utc=%s\n' "$created_utc"
  printf 'app_git_commit=%s\n' "$git_commit"
  printf 'alembic_revision=%s\n' "$alembic_revision"
  printf 'architecture=%s\n' "$(uname -m)"
  printf 'postgres_client=%s\n' "$(pg_dump --version)"
  printf 'database=otternest\n'
  printf 'artifact_directory=%s\n' "$ARTIFACT_DIR"
} > "${TMP_DIR}/manifest.txt"

(
  cd "$TMP_DIR"
  sha256sum database.dump artifacts.tar.gz > SHA256SUMS
)

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
archive_name="otternest-backup-${timestamp}.tar.gz"
archive_tmp="${BACKUP_DIR}/.${archive_name}.tmp"
tar -czf "$archive_tmp" -C "$TMP_DIR" database.dump artifacts.tar.gz manifest.txt SHA256SUMS
chmod 0600 "$archive_tmp"
mv -- "$archive_tmp" "${BACKUP_DIR}/${archive_name}"

find "$BACKUP_DIR" -maxdepth 1 -type f -name 'otternest-backup-*.tar.gz' \
  -mtime "+${RETENTION_DAYS}" -delete

echo "Created local OtterNest backup: ${BACKUP_DIR}/${archive_name}"
