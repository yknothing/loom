#!/bin/bash
# Setup daily cron job for Cognitive Flywheel
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CRON_CMD="0 6 * * * $PROJECT_DIR/scripts/daily-pipeline.sh >> $PROJECT_DIR/logs/cron.log 2>&1"

# Check if already exists
(crontab -l 2>/dev/null | grep -q "daily-pipeline") && {
  echo "Cron job already exists. Removing old one and adding new."
  crontab -l 2>/dev/null | grep -v "daily-pipeline" | { cat; echo "$CRON_CMD"; } | crontab -
} || {
  (crontab -l 2>/dev/null; echo "$CRON_CMD") | crontab -
  echo "Cron job added: daily at 06:00"
}
