#!/usr/bin/env bash
# Antigravity CLI Supervisor Launcher
# Usage: ./run-supervisor.sh [--watch] [supervisor options] [agy options]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# デフォルトの審査役モデル（環境変数 SUPERVISOR_MODEL が設定されていればそれを優先）
export SUPERVISOR_MODEL="${SUPERVISOR_MODEL:-gemini-3.8-flash-low}"

# --watch フラグの処理（GNOME Terminal でリアルタイム監査ログタブを連動起動）
SUPERVISOR_ARGS=()
for arg in "$@"; do
    if [[ "$arg" == "--watch" ]]; then
        if command -v gnome-terminal >/dev/null 2>&1; then
            gnome-terminal --tab --title="Supervisor Audit Log" -- "${SCRIPT_DIR}/watch-audit.sh" 2>/dev/null &
        fi
    else
        SUPERVISOR_ARGS+=("$arg")
    fi
done

exec python3 "${SCRIPT_DIR}/supervisor.py" "${SUPERVISOR_ARGS[@]}"
