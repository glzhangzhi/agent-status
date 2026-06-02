#!/bin/bash
# Start the Agent Status tmux Poller
# Usage:
#   ./start-poller.sh                    # auto-discover Copilot CLI sessions
#   ./start-poller.sh test-6 tavern-3    # monitor specific sessions
cd "$(dirname "$0")/tmux_poller"
exec python3 -u poller.py "$@"
