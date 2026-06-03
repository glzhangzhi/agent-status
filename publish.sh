#!/bin/bash
# Publish agent-status to GitHub
# Run this script once to authenticate, create the repo, and push.
set -e

cd "$(dirname "$0")"

echo "=== Step 1: Authenticate with GitHub ==="
if ! gh auth status &>/dev/null; then
    gh auth login --hostname github.com --git-protocol ssh --skip-ssh-key --web
fi
echo "✓ Authenticated"

echo ""
echo "=== Step 2: Create GitHub repo ==="
if gh repo view glzhangzhi/agent-status &>/dev/null; then
    echo "✓ Repo already exists"
else
    gh repo create agent-status --public --description "Real-time status monitoring for Copilot CLI agents in tmux — zero-intrusion screen scraping" --source . --push
    echo "✓ Repo created and pushed"
    echo ""
    echo "🎉 Published: https://github.com/glzhangzhi/agent-status"
    exit 0
fi

echo ""
echo "=== Step 3: Push to GitHub ==="
git push -u origin main
echo ""
echo "🎉 Published: https://github.com/glzhangzhi/agent-status"
