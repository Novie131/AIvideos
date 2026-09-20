#!/bin/bash
# AI Shorts Studio
cd "$(dirname "$0")"
exec uv run uvicorn studio.server:app --host 127.0.0.1 --port 8765 "$@"
