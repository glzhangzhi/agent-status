#!/bin/bash
# Start the Agent Status TUI
cd "$(dirname "$0")/tui"
exec python3 app.py
