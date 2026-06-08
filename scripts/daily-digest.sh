#!/usr/bin/env bash
# daily-digest.sh — Thin wrapper for curator.py
# Called by OpenClaw cron or manually
# Outputs digest to stdout for delivery

set -euo pipefail

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

WIKI_DIR="$(_cfg data.wiki_dir)"
DAILY_DIR="$WIKI_DIR/daily"

# Parse optional args
DAYS=1
TOP=10
while [[ $# -gt 0 ]]; do
    case "$1" in
        --days) DAYS="$2"; shift 2 ;;
        --top)  TOP="$2"; shift 2 ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

mkdir -p "$DAILY_DIR"
TODAY=$(date '+%Y-%m-%d')
OUTFILE="$DAILY_DIR/${TODAY}.md"

# Generate digest via curator.py
cd "$(_cfg data.data_dir)/.."
DIGEST=$(python3 "$SCRIPT_DIR/curator.py" --days "$DAYS" --top "$TOP" 2>/tmp/cf-digest-err.log) && RC=$? || RC=$?

if [ $RC -ne 0 ]; then
    ERRMSG=$(cat /tmp/cf-digest-err.log 2>/dev/null || echo "unknown error")
    echo "❌ Digest generation failed (rc=$RC): $ERRMSG" >&2
    exit 1
fi

# Save to daily file
{
    echo "---"
    echo "date: $TODAY"
    echo "type: daily-digest"
    echo "days: $DAYS"
    echo "top: $TOP"
    echo "generated: $(date '+%Y-%m-%dT%H:%M:%S')"
    echo "---"
    echo ""
    echo "$DIGEST"
} > "$OUTFILE"

# Append to log.md
{
    echo ""
    echo "---"
    echo ""
    echo "## [$TODAY] digest | Daily Digest (Top $TOP, ${DAYS}d)"
    echo "来源: scripts/curator.py"
    echo ""
    echo "$DIGEST"
} >> "$WIKI_DIR/log.md"

# Output to stdout for OpenClaw
echo "$DIGEST"
