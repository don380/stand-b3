#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
echo "===================================================="
echo "  reloops · Stand B3 Offline Server (Port 4310)"
echo "===================================================="
(sleep 1.2 && open "http://localhost:4310") &
exec python3 server.py
