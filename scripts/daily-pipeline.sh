#!/usr/bin/env bash
# daily-pipeline.sh — Cognitive Flywheel daily content pipeline
# Runs: rss-fetch → llm-ingest → daily-digest
# Designed to be called by OpenClaw cron at 8:00 AM

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

DATA_ROOT="$(_cfg data.data_dir)/.."

echo "🧠 Cognitive Flywheel — Daily Pipeline"
echo "========================================="
echo "Start: $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

# Step 1: Fetch new RSS articles
echo "📥 Step 1/3: Fetching RSS feeds..."
python3 "$SCRIPT_DIR/rss-fetch.py" --timeout 20 2>&1
echo ""

# Step 2: Ingest unprocessed articles into wiki
echo "⚙️  Step 2/3: Ingesting new articles..."
python3 "$SCRIPT_DIR/llm-ingest.py" --all-unprocessed 2>&1
echo ""

# Step 3: Generate weekly digest
echo "📊 Step 3/3: Generating digest..."
python3 "$SCRIPT_DIR/daily-digest.py" 2>&1
echo ""

# Step 4: Lint check
echo "🔍 Running wiki lint..."
python3 "$SCRIPT_DIR/wiki-lint.py" 2>&1 || true
echo ""

echo "========================================="
echo "Done: $(date '+%Y-%m-%d %H:%M:%S')"
