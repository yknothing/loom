#!/usr/bin/env bash
# full-pipeline.sh — End-to-end Cognitive Flywheel pipeline
#
# Flow: rss-fetch → enqueue-new → llm-ingest → sync-vault → curator-digest
#
# Design principles:
#   1. Each step is independent and idempotent
#   2. Each step has its own exit code — failures don't cascade
#   3. Step outputs are logged and verifiable
#   4. QA checks run after critical steps
#
# Exit codes: 0 = all steps ok, N = bitfield of failed steps (bit 0=fetch, 1=enqueue, 2=ingest, 3=sync, 4=digest)

set -uo pipefail

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

DATA_ROOT="$(_cfg data.data_dir)"
RAW_DIR="$(_cfg data.raw_dir)/rss"
DB="$(_cfg db_path)"
LOG_DIR="$(_cfg log_dir)"
VAULT_DIR="/Volumes/t7_shield/ObsidianVault/llmwiki"

TIMESTAMP=$(date '+%Y-%m-%d_%H%M%S')
PIPELINE_LOG="$LOG_DIR/pipeline-${TIMESTAMP}.log"

mkdir -p "$LOG_DIR"

FAILED=0

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$PIPELINE_LOG"; }

# ────────────────────────────────────────────
# Step 0: Pre-flight QA — verify environment
# ────────────────────────────────────────────
log "🧠 Cognitive Flywheel — Full Pipeline"
log "========================================"

if [ ! -f "$DB" ]; then
    log "❌ Database not found: $DB"
    exit 1
fi

BEFORE_PENDING=$(python3 -c "
import sqlite3
conn = sqlite3.connect('$DB')
cur = conn.cursor()
cur.execute('SELECT COUNT(*) FROM ingest_tasks WHERE status=\"pending\"')
print(cur.fetchone()[0])
conn.close()
")

BEFORE_DONE=$(python3 -c "
import sqlite3
conn = sqlite3.connect('$DB')
cur = conn.cursor()
cur.execute('SELECT COUNT(*) FROM ingest_tasks WHERE status=\"done\"')
print(cur.fetchone()[0])
conn.close()
")

BEFORE_RAW=$(ls "$RAW_DIR"/*.md 2>/dev/null | wc -l | tr -d ' ')

log "Pre-flight: raw=$BEFORE_RAW files | DB: done=$BEFORE_DONE, pending=$BEFORE_PENDING"
log ""

# ────────────────────────────────────────────
# Step 1: Fetch RSS
# ────────────────────────────────────────────
log "📥 Step 1/5: Fetching RSS feeds..."
FETCH_OUTPUT=$(python3 "$SCRIPT_DIR/rss-fetch.py" --timeout 20 2>&1) && FETCH_RC=$? || FETCH_RC=$?
echo "$FETCH_OUTPUT" | tee -a "$PIPELINE_LOG"

if [ $FETCH_RC -ne 0 ]; then
    log "⚠️  RSS fetch had errors (rc=$FETCH_RC), continuing..."
    FAILED=$((FAILED | 1))
fi

AFTER_RAW=$(ls "$RAW_DIR"/*.md 2>/dev/null | wc -l | tr -d ' ')
NEW_RAW=$((AFTER_RAW - BEFORE_RAW))
log "   Raw files: $BEFORE_RAW → $AFTER_RAW (+$NEW_RAW new)"
log ""

# ────────────────────────────────────────────
# Step 2: Enqueue new files into ingest_tasks
# ────────────────────────────────────────────
log "📋 Step 2/5: Enqueueing new raw files..."

ENQUEUE_OUTPUT=$(python3 - <<'PYEOF' 2>&1) && ENQ_RC=$? || ENQ_RC=$?
import sys, os
sys.path.insert(0, os.environ["SCRIPT_DIR"])
from pathlib import Path
from ingest.task_queue import TaskQueue
from ingest.config import db_path, raw_dir

RAW_DIR = raw_dir() / "rss"
DB_PATH = db_path()

queue = TaskQueue(str(DB_PATH))

# Find all .md files in raw/rss/
raw_files = sorted(str(f.resolve()) for f in RAW_DIR.glob("*.md"))
print(f"Found {len(raw_files)} raw files")

added, skipped = queue.init_queue(raw_files)
stats = queue.stats()
queue.close()

print(f"Enqueued: {added} new | Skipped: {skipped} existing")
print(f"Queue: pending={stats['pending']}, done={stats['done']}, total={stats['total']}")

if stats['pending'] == 0:
    print("✅ No new articles to ingest")
else:
    print(f"📥 {stats['pending']} articles ready for ingest")
PYEOF

echo "$ENQUEUE_OUTPUT" | tee -a "$PIPELINE_LOG"

if [ $ENQ_RC -ne 0 ]; then
    log "❌ Enqueue failed (rc=$ENQ_RC)"
    FAILED=$((FAILED | 2))
fi
log ""

# ────────────────────────────────────────────
# Step 3: LLM Ingest (only if there are pending tasks)
# ────────────────────────────────────────────
PENDING=$(python3 -c "
import sqlite3
conn = sqlite3.connect('$DB')
cur = conn.cursor()
cur.execute('SELECT COUNT(*) FROM ingest_tasks WHERE status=\"pending\"')
print(cur.fetchone()[0])
conn.close()
")

if [ "$PENDING" -gt 0 ]; then
    log "⚙️  Step 3/5: Running LLM ingest ($PENDING pending)..."

    # Use smart_runner for provider rotation
    cd "$DATA_ROOT/.."
    bash "$SCRIPT_DIR/ingest/smart_runner.sh" 2>&1 | tee -a "$PIPELINE_LOG" && INGEST_RC=$? || INGEST_RC=$?

    if [ $INGEST_RC -ne 0 ]; then
        log "⚠️  Ingest had issues (rc=$INGEST_RC)"
        FAILED=$((FAILED | 4))
    fi
else
    log "⚙️  Step 3/5: No pending articles, skipping ingest"
fi

AFTER_DONE=$(python3 -c "
import sqlite3
conn = sqlite3.connect('$DB')
cur = conn.cursor()
cur.execute('SELECT COUNT(*) FROM ingest_tasks WHERE status=\"done\"')
print(cur.fetchone()[0])
conn.close()
")

NEW_DONE=$((AFTER_DONE - BEFORE_DONE))
REMAINING=$(python3 -c "
import sqlite3
conn = sqlite3.connect('$DB')
cur = conn.cursor()
cur.execute('SELECT COUNT(*) FROM ingest_tasks WHERE status IN (\"pending\", \"failed\")')
print(cur.fetchone()[0])
conn.close()
")

log "   Ingest: $BEFORE_DONE → $AFTER_DONE done (+$NEW_DONE) | $REMAINING remaining"
log ""

# ────────────────────────────────────────────
# Step 4: Sync to Obsidian vault
# ────────────────────────────────────────────
log "🔄 Step 4/5: Syncing to Obsidian vault..."

if [ -d "$VAULT_DIR" ]; then
    WIKI_DIR="$(_cfg data.wiki_dir)"
    # Sync raw/
    rsync -av --delete "$RAW_DIR/../" "$VAULT_DIR/raw/" 2>&1 | tail -3 | tee -a "$PIPELINE_LOG"
    # Sync wiki/
    rsync -av --delete "$WIKI_DIR/" "$VAULT_DIR/wiki/" 2>&1 | tail -3 | tee -a "$PIPELINE_LOG"
    log "   ✅ Vault sync complete"
else
    log "   ⚠️  Vault not mounted ($VAULT_DIR), skipping sync"
    FAILED=$((FAILED | 8))
fi
log ""

# ────────────────────────────────────────────
# Step 5: Generate curated digest (TOP 10)
# ────────────────────────────────────────────
log "📰 Step 5/5: Generating curated digest..."

DIGEST_OUTPUT=$(python3 - <<'PYEOF' 2>&1) && DIG_RC=$? || DIG_RC=$?
import sys, os, json
sys.path.insert(0, os.environ["SCRIPT_DIR"])
from curator import generate_digest

digest = generate_digest(top_n=10)
print(digest)
PYEOF

echo "$DIGEST_OUTPUT" | tee -a "$PIPELINE_LOG"

if [ $DIG_RC -ne 0 ]; then
    log "⚠️  Digest generation failed (rc=$DIG_RC)"
    FAILED=$((FAILED | 16))
fi
log ""

# ────────────────────────────────────────────
# Step 5b: KB Reflect (cross-article synthesis)
# ────────────────────────────────────────────
log "🧩 Step 5b/5: Running KB reflector..."

DONE_COUNT=$(python3 -c "
import sqlite3
conn = sqlite3.connect('$DB')
cur = conn.cursor()
cur.execute('SELECT COUNT(*) FROM ingest_tasks WHERE status=\"done\"')
print(cur.fetchone()[0])
conn.close()
")

if [ "$DONE_COUNT" -ge 5 ]; then
    REFLECT_OUTPUT=$(cd "$DATA_ROOT/.." && python3 scripts/ingest/reflector.py --articles 30 --provider kimi 2>&1) && REFLECT_RC=$? || REFLECT_RC=$?
    echo "$REFLECT_OUTPUT" | tee -a "$PIPELINE_LOG"

    if [ $REFLECT_RC -ne 0 ]; then
        log "⚠️  Reflector had issues (rc=$REFLECT_RC), non-critical"
    fi
else
    log "   Skipping reflector (only $DONE_COUNT done articles, need >= 5)"
fi
log ""

# ────────────────────────────────────────────
# Post-flight QA
# ────────────────────────────────────────────
log "========================================"
log "📊 Pipeline Summary"
log "   Raw files: $AFTER_RAW (+$NEW_RAW)"
log "   Ingested:  $AFTER_DONE (+$NEW_DONE)"
log "   Remaining: $REMAINING"
log "   Failed bits: $FAILED"

if [ "$FAILED" -eq 0 ]; then
    log "✅ All steps completed successfully"
else
    log "⚠️  Some steps had issues (see above)"
fi

log "Done: $(date '+%Y-%m-%d %H:%M:%S')"
log "Log: $PIPELINE_LOG"

exit $FAILED
