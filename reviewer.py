"""Reviewer module that calls one-shot agy print mode (-p) with focused prompts."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from typing import Tuple

from rules import build_review_prompt

DEFAULT_MODEL = os.environ.get("SUPERVISOR_MODEL", "gemini-3.8-flash-low")
DEFAULT_TIMEOUT_SEC = 15.0


def review_command(
    command: str,
    context: str = "",
    model: str = DEFAULT_MODEL,
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
) -> Tuple[str, str]:
    """Execute agy -p as reviewer and robustly parse the decision.

    Returns:
        tuple[str, str]: (DECISION, REASON) where DECISION is "APPROVE" or "REJECT".
    """
    prompt = build_review_prompt(command, context)

    cmd_args = [
        "agy",
        "-p",
        prompt,
        "--model",
        model,
        "--effort",
        "low",
        "--print-timeout",
        f"{int(timeout_sec)}s",
    ]

    try:
        proc = subprocess.run(
            cmd_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_sec + 3.0,
            check=False,
        )

        output = proc.stdout.strip()
        stderr_output = proc.stderr.strip()

        # 1. 出力が空で stderr にエラーがある場合
        if not output and stderr_output:
            return "REJECT", f"審査役プロセスエラー ({stderr_output[:120]})"

        decision = None
        reason = ""

        # 2. 正規表現による柔軟な判定抽出 (大文字小文字・マークダウン記法対応)
        # DECISION: APPROVE / **DECISION**: APPROVE / 判定: APPROVE
        m_dec = re.search(r"(?:DECISION|判定|Verdict)[\s*:]+([A-Z]+)", output, re.IGNORECASE)
        if m_dec:
            verdict = m_dec.group(1).upper()
            if "APPROVE" in verdict:
                decision = "APPROVE"
            elif "REJECT" in verdict:
                decision = "REJECT"

        # 3. 本文全体からの APPROVE / REJECT フォールバック検出
        if decision is None:
            if "DECISION: APPROVE" in output or "APPROVE" in output.split():
                decision = "APPROVE"
            elif "DECISION: REJECT" in output or "REJECT" in output.split():
                decision = "REJECT"

        # 4. 理由の抽出
        m_reason = re.search(r"(?:REASON|理由|根拠)[\s*:]+(.+)", output, re.IGNORECASE)
        if m_reason:
            reason = m_reason.group(1).strip()
        else:
            # REASON: キーワードがない場合、DECISION 行以外の文章を理由として採用
            non_dec_lines = [
                line.strip()
                for line in output.splitlines()
                if line.strip() and not re.search(r"(?:DECISION|判定)", line, re.IGNORECASE)
            ]
            if non_dec_lines:
                reason = " ".join(non_dec_lines)[:150]

        # 5. デフォルトフォールバック
        if decision is None:
            # 形式不明だがエラーなく出力されている場合
            decision = "REJECT"
            reason = f"審査役AIの出力形式解析失敗: {output[:100]}" if output else "審査役からの応答が空でした。"
        elif not reason:
            reason = "規約ルール準拠を確認しました。" if decision == "APPROVE" else "潜在的セキュリティリスクが懸念されるため拒否しました。"

        return decision, reason

    except subprocess.TimeoutExpired:
        return "REJECT", f"審査役AIの応答がタイムアウト（{timeout_sec}秒超過）したため安全側に倒して拒否しました。"
    except Exception as e:
        return "REJECT", f"審査役プロセスの実行時エラー: {e}"
