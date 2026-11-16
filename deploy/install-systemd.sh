#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this installer as root, for example: sudo bash deploy/install-systemd.sh" >&2
  exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DIR="${SYSTEMD_UNIT_DIR:-/etc/systemd/system}"

install -d -m 0755 "${UNIT_DIR}"
for unit in \
  otternest-api.service \
  otternest-migrate.service \
  otternest-scheduler.service \
  otternest-workflows.service \
  otternest-webhooks.service
do
  install -o root -g root -m 0644 "${SCRIPT_DIR}/systemd/${unit}" "${UNIT_DIR}/${unit}"
done

systemctl daemon-reload
systemctl enable otternest-api.service otternest-scheduler.service otternest-workflows.service

echo "OtterNest units installed."
echo "API, scheduler and workflows are enabled but not started."
echo "Run otternest-migrate.service manually after configuring the environment."
echo "Webhook delivery remains disabled until its signing key and host allowlist are configured."
