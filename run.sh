#!/usr/bin/env bash
# Start Playlist Sorter on macOS / Linux:  ./run.sh   (or: bash run.sh)
set -e
cd "$(dirname "$0")"

PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then
    echo "Python 3 was not found. Install it from https://www.python.org/downloads/" >&2
    exit 1
fi

if ! "$PY" -c "import googleapiclient, google_auth_oauthlib" 2>/dev/null; then
    echo "Installing required packages (first run only)..."
    "$PY" -m pip install -r requirements.txt
fi

exec "$PY" -m playlist_sorter "$@"
