#!/bin/bash
# Sync wiki content to Obsidian vault
SRC="/Users/th/.openclaw/workspace-leader/cognitive-flywheel/wiki/"
DST="/Volumes/t7_shield/ObsidianVault/llmwiki/"

if [ ! -d "$DST" ]; then
  echo "❌ Vault not mounted: $DST"
  exit 1
fi

rsync -av --delete "$SRC" "$DST"
echo "✅ Sync complete at $(date)"
