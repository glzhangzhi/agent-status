#!/bin/bash
# Start the Agent Status Service
cd "$(dirname "$0")/status_service"
exec python3 app.py
