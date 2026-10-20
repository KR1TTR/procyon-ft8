#!/usr/bin/env bash
# Start Procyon FT8. Pass --demo to try it with a simulated band.
cd "$(dirname "$0")" && exec python3 -m procyon "$@"
