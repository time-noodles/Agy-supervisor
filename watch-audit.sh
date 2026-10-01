#!/usr/bin/env bash
# Antigravity Supervisor リアルタイム審査ログビューア
LOG_FILE="${HOME}/.gemini/supervisor/audit.log"
mkdir -p "${HOME}/.gemini/supervisor"
touch "${LOG_FILE}"

# ターミナルタイトルを設定
echo -ne "\033]0;Supervisor リアルタイム監査ログ\007"

clear
echo -e "\033[1;35m=====================================================\033[0m"
echo -e "\033[1;36m   Antigravity Supervisor リアルタイム審査ログ\033[0m"
echo -e "\033[1;35m=====================================================\033[0m"
echo -e "ファイル: \033[1;34m${LOG_FILE}\033[0m"
echo -e "（全コマンドの承認/拒否・判定理由・ハッシュ検証が記録されます）\n"

exec tail -f -n 30 "${LOG_FILE}"
