#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
URL=http://127.0.0.1:8765/
LOG_DIR="$HOME/Library/Logs/Personal Money Tracker"
LOG_FILE="$LOG_DIR/server.log"

if ! /usr/bin/curl --silent --fail "$URL" >/dev/null 2>&1; then
    UV_BIN=$(command -v uv || true)
    if [ -z "$UV_BIN" ]; then
        for candidate in "$HOME/.local/bin/uv" /opt/homebrew/bin/uv /usr/local/bin/uv; do
            if [ -x "$candidate" ]; then UV_BIN=$candidate; break; fi
        done
    fi
    if [ -z "$UV_BIN" ]; then
        /usr/bin/osascript -e 'display dialog "uv is not installed or could not be found. Install uv, then open Money Tracker.app again." buttons {"OK"} with icon caution'
        exit 1
    fi
    /bin/mkdir -p "$LOG_DIR"
    cd "$PROJECT_DIR"
    /usr/bin/nohup "$UV_BIN" run money-tracker >>"$LOG_FILE" 2>&1 </dev/null &
    attempt=0
    while [ "$attempt" -lt 40 ]; do
        if /usr/bin/curl --silent --fail "$URL" >/dev/null 2>&1; then break; fi
        /bin/sleep 0.5
        attempt=$((attempt + 1))
    done
fi

if ! /usr/bin/curl --silent --fail "$URL" >/dev/null 2>&1; then
    /usr/bin/osascript -e "display dialog \"Money Tracker did not start. Check $LOG_FILE for details.\" buttons {\"OK\"} with icon caution"
    exit 1
fi

/usr/bin/open "$URL"
