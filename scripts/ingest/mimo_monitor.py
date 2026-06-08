#!/usr/bin/env python3
"""
mimo_monitor.py — Probe Mimo SGP cluster and report status.
Outputs: reachable/unreachable + latency + success rate estimate.
"""
import urllib.request
import os
import time
import json
import sys

# Bypass proxy for Mimo
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
url = "https://token-plan-sgp.xiaomimimo.com/v1/models"

results = []
attempts = 3

for i in range(attempts):
    t0 = time.time()
    try:
        req = urllib.request.Request(url)
        with opener.open(req, timeout=15) as resp:
            latency = (time.time() - t0) * 1000
            results.append({"ok": True, "latency_ms": round(latency), "status": resp.status})
    except urllib.error.HTTPError as e:
        latency = (time.time() - t0) * 1000
        if e.code in (401, 403):
            results.append({"ok": True, "latency_ms": round(latency), "status": e.code, "note": "auth=reachable"})
        else:
            results.append({"ok": False, "latency_ms": round(latency), "error": f"HTTP {e.code}"})
    except Exception as e:
        latency = (time.time() - t0) * 1000
        results.append({"ok": False, "latency_ms": round(latency), "error": f"{type(e).__name__}: {str(e)[:60]}"})
    
    if i < attempts - 1:
        time.sleep(2)

ok_count = sum(1 for r in results if r["ok"])
avg_latency = round(sum(r["latency_ms"] for r in results if r["ok"]) / max(ok_count, 1))

status = "🟢 REACHABLE" if ok_count > 0 else "🔴 DOWN"
print(f"{status} ({ok_count}/{attempts} ok, avg {avg_latency}ms)")
for r in results:
    icon = "✅" if r["ok"] else "❌"
    detail = r.get("error", f"HTTP {r.get('status','?')}")
    print(f"  {icon} {r['latency_ms']}ms - {detail}")

# Exit code: 0 = reachable, 1 = down
sys.exit(0 if ok_count > 0 else 1)
