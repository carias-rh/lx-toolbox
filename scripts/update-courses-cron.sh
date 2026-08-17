#!/bin/bash
# update-courses-cron.sh – Scrape the ROL catalog, update courses-list.txt,
# commit and push to GitHub.
#
# Intended to run monthly via the systemd user timer (see systemd/install.sh).

set -euo pipefail

# --- Resolve paths ---------------------------------------------------------
SOURCE="${BASH_SOURCE[0]}"
while [[ -L "$SOURCE" ]]; do
    DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
    SOURCE="$(readlink "$SOURCE")"
    [[ "$SOURCE" != /* ]] && SOURCE="$DIR/$SOURCE"
done
SCRIPT_DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
LX_TOOLBOX_DIR="$(dirname "$SCRIPT_DIR")"

# --- Find lx-tool ----------------------------------------------------------
if [[ -x "${SCRIPT_DIR}/lx-tool" ]]; then
    LX_TOOL="${SCRIPT_DIR}/lx-tool"
elif command -v lx-tool &>/dev/null; then
    LX_TOOL="lx-tool"
elif [[ -x "/opt/lx-toolbox/scripts/lx-tool" ]]; then
    LX_TOOL="/opt/lx-toolbox/scripts/lx-tool"
else
    echo "$(date '+%F %T') ERROR: lx-tool not found" >&2
    exit 1
fi

# --- Run --------------------------------------------------------------------
echo "$(date '+%F %T') Starting courses-list.txt update…"

"$LX_TOOL" lab update-courses --headless --commit

echo "$(date '+%F %T') Done."
