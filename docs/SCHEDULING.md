# Scheduling the Cognitive Flywheel Pipeline

## Why NOT System Crontab

System `crontab` was evaluated and **rejected** for this project. The reasons:

1. **Gateway SIGTERM storms**: OpenClaw gateway restarts send SIGTERM to all child processes. Crontab-spawned jobs get killed mid-pipeline with no checkpoint/resume capability. This happened repeatedly in production.

2. **No progress tracking**: A killed crontab job leaves no record of what completed. The next run either duplicates work or skips it blindly.

3. **No delivery channel**: Crontab output goes to mail or `/dev/null`. We need the digest delivered to the user (via OpenClaw cron → Telegram).

4. **Environment fragility**: Shell PATH, Python venv, working directory — all need manual setup in crontab. OpenClaw cron provides a consistent environment.

## Recommended: OpenClaw Cron

Use OpenClaw's built-in cron system. It runs tasks as agent turns, which means:

- **Survives gateway restarts** — jobs are queued and dispatched by the gateway, not by a shell process
- **Deliverable output** — agent can call `daily-digest.sh` and relay the digest via Telegram
- **Isolation** — each run is an `isolated` session, so it doesn't pollute the main session context
- **Proper environment** — the agent runs in the project workspace with correct PATH

## Cron Job Design

### Job 1: Morning Pipeline (06:00 PDT)

**Schedule**: `0 6 * * *` (America/Los_Angeles)

**Purpose**: Full pipeline — fetch new articles, ingest with LLM, generate daily digest, deliver.

**Agent prompt**:

```
Run the Cognitive Flywheel morning pipeline. Working directory: /Users/th/.openclaw/workspace-leader/cognitive-flywheel/

Steps:
1. Run `bash scripts/full-pipeline.sh` — this does: RSS fetch → enqueue → LLM ingest → vault sync → digest
2. After pipeline completes, run `bash scripts/daily-digest.sh` to generate and save the daily digest
3. Send me the digest output (it comes from stdout of daily-digest.sh)

If the pipeline fails, report which step failed and the error. Do NOT retry automatically.
```

**OpenClaw cron config**:
```json
{
  "schedule": "0 6 * * *",
  "timezone": "America/Los_Angeles",
  "payload": {
    "kind": "agentTurn",
    "message": "<the prompt above>"
  },
  "sessionTarget": "isolated"
}
```

### Job 2: Evening Prefetch (18:00 PDT)

**Schedule**: `0 18 * * *` (America/Los_Angeles)

**Purpose**: Fetch RSS and enqueue only — no LLM ingest. Prepopulates the queue so the morning pipeline has less fetching to do.

**Agent prompt**:

```
Run the Cognitive Flywheel evening prefetch. Working directory: /Users/th/.openclaw/workspace-leader/cognitive-flywheel/

Steps:
1. Run `python3 scripts/rss-fetch.py --timeout 20`
2. Run `python3 scripts/enqueue-new.py`

Report how many new articles were fetched and enqueued. Do NOT run LLM ingest.
```

**OpenClaw cron config**:
```json
{
  "schedule": "0 18 * * *",
  "timezone": "America/Los_Angeles",
  "payload": {
    "kind": "agentTurn",
    "message": "<the prompt above>"
  },
  "sessionTarget": "isolated"
}
```

## Key Design Principles

### Agent calls scripts, never inline code

The agent turn should **call existing scripts**, not run inline Python or shell code. This ensures:
- Scripts are version-controlled and testable
- Agent prompt changes don't break logic
- The same scripts can be run manually for debugging

### `sessionTarget: "isolated"`

Each cron run creates a fresh isolated session. This prevents:
- Context pollution between runs
- Token accumulation from previous runs
- Cascading failures from stale state

### No long-running inline tasks

The agent turn should delegate to scripts quickly. The scripts handle their own:
- Error handling and logging
- Progress tracking (via SQLite DB)
- Idempotency (re-running is safe)

## Architecture Diagram

```
┌─────────────────────────────────────────────────────┐
│                   OpenClaw Gateway                   │
│                                                      │
│  ┌──────────┐     ┌──────────────────────────────┐  │
│  │ Cron:    │────▶│ Isolated Agent Session        │  │
│  │ 06:00    │     │                               │  │
│  │ PDT      │     │  1. full-pipeline.sh          │  │
│  │          │     │  2. daily-digest.sh           │  │
│  │          │     │  3. Deliver digest to user    │  │
│  └──────────┘     └──────────────────────────────┘  │
│                                                      │
│  ┌──────────┐     ┌──────────────────────────────┐  │
│  │ Cron:    │────▶│ Isolated Agent Session        │  │
│  │ 18:00    │     │                               │  │
│  │ PDT      │     │  1. rss-fetch.py              │  │
│  │          │     │  2. enqueue-new.py            │  │
│  │          │     │  3. Report counts to user     │  │
│  └──────────┘     └──────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

## Setup Checklist

> ⚠️ **Requires Bruce's approval** before creating cron jobs.

- [ ] Confirm `full-pipeline.sh` runs cleanly end-to-end
- [ ] Confirm `daily-digest.sh` produces correct Top 10 output
- [ ] Test `--since-last` mode in `curator.py` with state file
- [ ] Get approval to create the two cron jobs
- [ ] Create morning pipeline cron via OpenClaw config
- [ ] Create evening prefetch cron via OpenClaw config
- [ ] Monitor first 3 runs of each for stability
- [ ] Document any adjustments to timing or prompts

## Troubleshooting

### Pipeline fails at ingest step
- Check provider health: `python3 -c "import urllib.request; urllib.request.urlopen('https://token-plan-sgp.xiaomimimo.com/v1/models', timeout=10)"`
- Check `data/task-queue.db` for stuck tasks: `SELECT status, COUNT(*) FROM ingest_tasks GROUP BY status`
- Reset stuck tasks: `UPDATE ingest_tasks SET status='pending', started_at=NULL WHERE status='running'`

### Digest shows old articles
- Verify `curator.py --since-last` mode is being used
- Check `data/digest-state.json` has the correct timestamp
- Manually update: `echo '{"last_digest_ts":"2026-06-05T12:00:00"}' > data/digest-state.json`

### Cron job doesn't fire
- Check OpenClaw gateway status: `openclaw gateway status`
- Check cron config is loaded: review gateway config
- Verify timezone is correct
