#!/bin/bash
# Setup daily cron job for Cognitive Flywheel
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ── Load paths from config/loom.yml ──────────────────────────
_cfg() {
  python3 -c "
import yaml, sys
d = yaml.safe_load(open('${SCRIPT_DIR}/../config/loom.yml'))
keys = '$1'.split('.')
for k in keys:
    d = d.get(k, {}) if isinstance(d, dict) else None
print(d or '')
"
}

LOG_DIR="$(_cfg log_dir)"
CRON_CMD="0 6 * * * ${SCRIPT_DIR}/daily-pipeline.sh >> ${LOG_DIR}/cron.log 2>&1"

# Check if already exists
(crontab -l 2>/dev/null | grep -q "daily-pipeline") && {
  echo "Cron job already exists. Removing old one and adding new."
  crontab -l 2>/dev/null | grep -v "daily-pipeline" | { cat; echo "$CRON_CMD"; } | crontab -
} || {
  (crontab -l 2>/dev/null; echo "$CRON_CMD") | crontab -
  echo "Cron job added: daily at 06:00"
}
