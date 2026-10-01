"""Command Focus Extractor for Antigravity Supervisor.

Extracts the essential operational payload from complex command lines
(stripping repetitive SSH flags, wrappers, etc.) and performs pre-flight
static scanning so the reviewer AI can focus exclusively on critical risk points
in minimal tokens and under 2 seconds.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CommandFocus:
    raw_command: str
    execution_type: str  # "local", "ssh_remote", "wrapped"
    target_host: str = ""
    core_operations: list[str] = field(default_factory=list)
    key_targets: list[str] = field(default_factory=list)
    static_alerts: list[str] = field(default_factory=list)

    def to_focus_summary(self) -> str:
        """Generate a concise, token-efficient text summary focusing on critical parts."""
        lines = []
        if self.execution_type == "ssh_remote":
            lines.append(f"■ 実行形態: リモートSSH実行 (ターゲット: {self.target_host})")
        elif self.execution_type == "wrapped":
            lines.append("■ 実行形態: シェルラッパー経由実行")
        else:
            lines.append("■ 実行形態: ローカル環境実行")

        lines.append("■ 実行されるコア操作:")
        if self.core_operations:
            for idx, op in enumerate(self.core_operations, 1):
                lines.append(f"  {idx}. {op}")
        else:
            lines.append(f"  1. {self.raw_command[:100]}")

        if self.key_targets:
            lines.append(f"■ 操作対象ファイル/パス: {', '.join(self.key_targets[:5])}")

        if self.static_alerts:
            lines.append(f"■ 静的スキャン警告: {', '.join(self.static_alerts)}")
        else:
            lines.append("■ 静的スキャン事前判定: 既知の破壊・特権・機密漏洩・無限ループは不検出 (CLEAN)")

        return "\n".join(lines)


def _scan_static_risks(cmd_str: str) -> list[str]:
    """Perform fast local regex scan for known security risks."""
    alerts = []
    # 1. 破壊的削除
    if re.search(r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f*|-[a-zA-Z]*f[a-zA-Z]*r*)\s+[\*\/\~]", cmd_str):
        alerts.append("危険な再帰的一括削除(rm -rf)")
    if "git reset --hard" in cmd_str or "git clean -f" in cmd_str:
        alerts.append("不可逆なGit強制巻き戻し/消去")

    # 2. 特権・システム改変
    if re.search(r"(?:^|[|;&]\s*)sudo\b", cmd_str):
        alerts.append("特権昇格(sudo)")
    if re.search(r"\b(reboot|poweroff|shutdown|init\s+0|init\s+6)\b", cmd_str):
        alerts.append("システム再起動/シャットダウン")
    if re.search(r"\bchmod\s+(?:-R\s+)?(?:777|a\+rwx)\b", cmd_str):
        alerts.append("危険なパーミッション全開放(chmod 777)")

    # 3. 機密情報漏洩
    if re.search(r"(?:^|[|;&]\s*)(?:env|printenv)\b(?:\s*$|\s*[|&;])", cmd_str):
        alerts.append("全環境変数のコンソール一括出力")
    if re.search(r"\bcat\s+~?/?\.ssh/", cmd_str):
        alerts.append("SSH秘密鍵ファイルの直接閲覧")

    # 4. 無限ループ
    if re.search(r"\bwhile\s+(?:true|:)\s*;", cmd_str) or "for ((;;))" in cmd_str:
        alerts.append("無限ループ構文")

    return alerts


def extract_command_focus(command: str, cwd: str | Path | None = None) -> CommandFocus:
    """Extract focal points from a command line by unwrapping SSH and wrappers."""
    raw = command.strip()
    alerts = _scan_static_risks(raw)

    # 1. SSH コマンドのアンラップ判定
    # 例: ssh ... user@host "remote_cmd"
    if raw.startswith("ssh ") or " ssh " in raw or raw.startswith("scp "):
        m_ssh = re.search(
            r"ssh(?:\s+-[a-zA-Z0-9_-]+(?:\s+[^ -][^\s]*)?)*\s+([a-zA-Z0-9_.-]+@[a-zA-Z0-9_.-]+)\s+[\"']([^\"']+)[\"']",
            raw,
        )
        if m_ssh:
            target_host = m_ssh.group(1)
            remote_payload = m_ssh.group(2).strip()

            # リモートペイロードのサブコマンド分割
            ops = [op.strip() for op in re.split(r"\s*(?:&&|\|\||;)\s*", remote_payload) if op.strip()]

            # ターゲットパス・スクリプトの抽出
            targets = []
            for op in ops:
                for part in op.split():
                    if part.endswith((".py", ".sh", ".bash", ".json", ".csv", ".txt", ".log")) or part.startswith(("/", "~", "./")):
                        if part not in targets and not part.startswith("-"):
                            targets.append(part)

            return CommandFocus(
                raw_command=raw,
                execution_type="ssh_remote",
                target_host=target_host,
                core_operations=ops,
                key_targets=targets,
                static_alerts=alerts,
            )

    # 2. シェルラッパー (bash -c / sh -c) のアンラップ
    m_shell = re.search(r"(?:bash|sh|zsh)\s+-c\s+[\"']([^\"']+)[\"']", raw)
    if m_shell:
        payload = m_shell.group(1).strip()
        ops = [op.strip() for op in re.split(r"\s*(?:&&|\|\||;)\s*", payload) if op.strip()]
        return CommandFocus(
            raw_command=raw,
            execution_type="wrapped",
            core_operations=ops,
            static_alerts=alerts,
        )

    # 3. 通常のローカルコマンド
    # 複合コマンド (&&, ||, ;, |) の分割
    ops = [op.strip() for op in re.split(r"\s*(?:&&|\|\||;)\s*", raw) if op.strip()]

    # 重要ターゲットの抽出
    targets = []
    for op in ops:
        for part in op.split():
            if part.endswith((".py", ".sh", ".bash", ".json", ".csv", ".txt", ".log")) or part.startswith(("/", "~", "./")):
                if part not in targets and not part.startswith("-"):
                    targets.append(part)

    return CommandFocus(
        raw_command=raw,
        execution_type="local",
        core_operations=ops,
        key_targets=targets,
        static_alerts=alerts,
    )
