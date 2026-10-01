#!/usr/bin/env bash
# Antigravity Supervisor Linux/macOS Installer
# Adds 'agy-c' and 'agy-sup' aliases to ~/.bashrc or ~/.zshrc

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNNER_SH="${SCRIPT_DIR}/run-supervisor.sh"

echo -e "\033[1;36m==================================================\033[0m"
echo -e "\033[1;36m Antigravity Supervisor Installer (Linux / macOS)\033[0m"
echo -e "\033[1;36m==================================================\033[0m"

# 1. 実行権限の付与
chmod +x "${RUNNER_SH}"
if [ -f "${SCRIPT_DIR}/watch-audit.sh" ]; then
    chmod +x "${SCRIPT_DIR}/watch-audit.sh"
fi
echo -e "\033[1;32m[✓] Executable permissions set for run-supervisor.sh\033[0m"

# 2. シェル設定ファイルの判定
TARGET_RC=""
if [ -n "$ZSH_VERSION" ] || [ -f "$HOME/.zshrc" ]; then
    TARGET_RC="$HOME/.zshrc"
elif [ -f "$HOME/.bashrc" ]; then
    TARGET_RC="$HOME/.bashrc"
else
    TARGET_RC="$HOME/.profile"
fi

MARKER_START="# >>> Antigravity Supervisor >>>"
MARKER_END="# <<< Antigravity Supervisor <<<"

ALIAS_BLOCK="${MARKER_START}
alias agy-c=\"${RUNNER_SH}\"
alias agy-sup=\"${RUNNER_SH}\"
${MARKER_END}"

if grep -q "${MARKER_START}" "${TARGET_RC}" 2>/dev/null; then
    # 既存の設定を更新
    awk -v s="${MARKER_START}" -v e="${MARKER_END}" -v repl="${ALIAS_BLOCK}" '
        $0 ~ s { skip=1; print repl; next }
        $0 ~ e { skip=0; next }
        !skip { print }
    ' "${TARGET_RC}" > "${TARGET_RC}.tmp" && mv "${TARGET_RC}.tmp" "${TARGET_RC}"
    echo -e "\033[1;32m[✓] Updated existing Antigravity Supervisor aliases in ${TARGET_RC}\033[0m"
else
    echo -e "\n${ALIAS_BLOCK}" >> "${TARGET_RC}"
    echo -e "\033[1;32m[✓] Added 'agy-c' and 'agy-sup' aliases to ${TARGET_RC}\033[0m"
fi

echo -e "\n\033[1;32mInstallation Complete! 🎉\033[0m"
echo -e "\033[1;33mReload your shell configuration with:\033[0m source ${TARGET_RC}"
echo -e "\033[1;36mThen run 'agy-c' to launch Antigravity with AI Supervisor!\033[0m\n"
