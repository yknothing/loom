#!/bin/bash
# smart_runner.sh — Auto-rotating ingest runner
# Strategy: Mimo priority → Kimi fallback → 10min poll when both fail
# Bypasses proxy for Mimo (direct connect), uses proxy for Kimi

set -euo pipefail
cd "$(dirname "$0")/../.."

DB="data/task-queue.db"
LOG="/tmp/ingest-smart.log"
MAX_BATCH=200
DELAY=5
MAX_RETRIES=5

provider=""
consecutive_failures=0
MAX_CONSECUTIVE=3

echo "🧠 Smart Ingest Runner started at $(date)" | tee -a "$LOG"

check_pending() {
    python3 -c "
import sqlite3
conn = sqlite3.connect('$DB')
cur = conn.cursor()
cur.execute('SELECT COUNT(*) FROM ingest_tasks WHERE status=\"pending\"')
print(cur.fetchone()[0])
conn.close()
"
}

probe_provider() {
    local prov="$1"
    python3 -c "
import urllib.request, os, time, json

prov = '$prov'
if prov == 'mimo':
    # Direct connect, no proxy
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    url = 'https://token-plan-sgp.xiaomimimo.com/v1/models'
else:
    opener = None
    url = 'https://api.kimi.com/coding/v1/models'

req = urllib.request.Request(url)
req.add_header('Content-Type', 'application/json')
t0 = time.time()
try:
    if opener:
        resp = opener.open(req, timeout=10)
    else:
        resp = urllib.request.urlopen(req, timeout=10)
    elapsed = (time.time() - t0) * 1000
    print(f'OK {resp.status} {elapsed:.0f}ms')
except urllib.error.HTTPError as e:
    elapsed = (time.time() - t0) * 1000
    if e.code in (401, 403, 429):
        print(f'OK {e.code} {elapsed:.0f}ms (auth/ratelimit = server reachable)')
    else:
        print(f'FAIL HTTP {e.code}')
except Exception as e:
    print(f'FAIL {type(e).__name__}: {str(e)[:80]}')
" 2>&1
}

run_batch() {
    local prov="$1"
    local count
    count=$(check_pending)
    local batch=$((count < MAX_BATCH ? count : MAX_BATCH))
    
    if [ "$batch" -le 0 ]; then
        echo "✅ No pending tasks!" | tee -a "$LOG"
        exit 0
    fi
    
    echo "📦 Running $batch articles with $prov ($(date +%H:%M:%S))" | tee -a "$LOG"
    
    python3 -u -m scripts.ingest.parallel_runner \
        --provider "$prov" \
        --delay "$DELAY" \
        --max-retries "$MAX_RETRIES" \
        --max "$batch" \
        2>&1 | tee -a "$LOG"
    
    return $?
}

# Main loop
while true; do
    pending=$(check_pending)
    if [ "$pending" -le 0 ]; then
        echo "✅ All tasks complete!" | tee -a "$LOG"
        break
    fi
    
    echo "" | tee -a "$LOG"
    echo "⏳ $pending pending | Probing providers... ($(date +%H:%M:%S))" | tee -a "$LOG"
    
    # Try Mimo first
    result=$(probe_provider "mimo")
    echo "  Mimo probe: $result" | tee -a "$LOG"
    
    if echo "$result" | grep -q "^OK"; then
        echo "  → Using Mimo" | tee -a "$LOG"
        run_batch "mimo"
        consecutive_failures=0
        continue
    fi
    
    # Fallback to Kimi
    result=$(probe_provider "kimi")
    echo "  Kimi probe: $result" | tee -a "$LOG"
    
    if echo "$result" | grep -q "^OK"; then
        echo "  → Using Kimi" | tee -a "$LOG"
        run_batch "kimi"
        consecutive_failures=0
        continue
    fi
    
    # Both failed
    consecutive_failures=$((consecutive_failures + 1))
    echo "🔴 Both providers failed ($consecutive_failures consecutive) — waiting 10min" | tee -a "$LOG"
    sleep 600
done

echo "🏁 Smart Runner finished at $(date)" | tee -a "$LOG"
