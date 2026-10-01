"""Context and Diff extractor for Antigravity Supervisor.

Extracts exact pending tool calls, recent user intent, tool calls, and file edit diffs from:
1. Conversation transcript (~/.gemini/antigravity-cli/brain/<conv_id>/.../transcript.jsonl)
2. Git diff fallback (if in git repository)
3. Live terminal buffer fallback

This context provides 100% accurate command identification and dramatically improves
reviewer AI accuracy and reduces hesitation/inference time.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

BRAIN_ROOT = Path.home() / ".gemini" / "antigravity-cli" / "brain"


def find_latest_transcript(conv_id: str | None = None) -> Path | None:
    """Locate the active transcript.jsonl file for the current or latest conversation."""
    if conv_id:
        target = BRAIN_ROOT / conv_id / ".system_generated" / "logs" / "transcript.jsonl"
        if target.exists():
            return target

    try:
        candidates = sorted(
            BRAIN_ROOT.glob("*/.system_generated/logs/transcript.jsonl"),
            key=os.path.getmtime,
            reverse=True,
        )
        if candidates:
            return candidates[0]
    except Exception:
        pass
    return None


def get_pending_tool_call(conv_id: str | None = None) -> dict | None:
    """Retrieve the pending run_command tool call directly from the transcript."""
    tpath = find_latest_transcript(conv_id)
    if not tpath or not tpath.exists():
        return None

    try:
        with open(tpath, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()

        for line in reversed(lines[-20:]):
            data = json.loads(line)
            tc = data.get("tool_calls")
            if tc and isinstance(tc, list):
                for call in tc:
                    if call.get("name") == "run_command":
                        args = call.get("args", {})
                        cmd = args.get("CommandLine", "").strip("\"'")
                        cwd = args.get("Cwd", "").strip("\"'")
                        summary = args.get("toolSummary", "").strip("\"'")
                        action = args.get("toolAction", "").strip("\"'")
                        step_idx = data.get("step_index")
                        if cmd:
                            return {
                                "name": "run_command",
                                "cmd": cmd,
                                "cwd": cwd,
                                "summary": summary,
                                "action": action,
                                "step_index": step_idx,
                            }
    except Exception:
        pass
    return None


def get_recent_diff_for_file(
    file_name: str, cwd: str | Path | None = None, conv_id: str | None = None
) -> str | None:
    """Extract recent diff for a specific file from transcript or git diff."""
    clean_target = Path(file_name).name
    tpath = find_latest_transcript(conv_id)

    # 1. transcript.jsonl からの差分抽出（replace_file_content の diff_block）
    if tpath and tpath.exists():
        try:
            with open(tpath, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()

            for line in reversed(lines[-35:]):
                data = json.loads(line)
                content = data.get("content", "")
                if "[diff_block_start]" in content and clean_target in content:
                    diff_part = (
                        content.split("[diff_block_start]")[1]
                        .split("[diff_block_end]")[0]
                        .strip()
                    )
                    diff_lines = diff_part.splitlines()
                    if len(diff_lines) > 60:
                        diff_part = "\n".join(diff_lines[:50]) + f"\n... (他 {len(diff_lines)-50} 行省略)"
                    return diff_part
        except Exception:
            pass

    # 2. git diff からの差分抽出（Git管理下の場合）
    if cwd:
        try:
            target_path = (Path(cwd) / file_name).resolve()
            if target_path.exists():
                res = subprocess.run(
                    ["git", "diff", "--", str(target_path)],
                    cwd=str(cwd),
                    capture_output=True,
                    text=True,
                    timeout=2.0,
                )
                if res.returncode == 0 and res.stdout.strip():
                    diff_lines = res.stdout.strip().splitlines()
                    if len(diff_lines) > 60:
                        return "\n".join(diff_lines[:50]) + f"\n... (他 {len(diff_lines)-50} 行省略)"
                    return res.stdout.strip()
        except Exception:
            pass

    return None


def extract_recent_diff_context(
    conv_id: str | None = None,
    cmd: str = "",
    cwd: str | Path | None = None,
    terminal_buffer: str = "",
) -> str:
    """Extract recent user request, file modifications, diffs, and context for AI reviewer."""
    context_lines: list[str] = []

    # 実行対象スクリプトの差分を検索
    if cmd:
        from decision_cache import extract_script_file
        script_file = extract_script_file(cmd, cwd)
        if script_file:
            script_diff = get_recent_diff_for_file(script_file.name, cwd=cwd, conv_id=conv_id)
            if script_diff:
                context_lines.append(f"【実行スクリプト直前の変更差分（diff）: {script_file.name}】:\n{script_diff}")

    # 会話履歴からのユーザー要求・直前アクションの抽出
    tpath = find_latest_transcript(conv_id)
    if tpath and tpath.exists():
        try:
            with open(tpath, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()

            recent_events = []
            for line in reversed(lines[-25:]):
                data = json.loads(line)
                stype = data.get("type")
                tc = data.get("tool_calls")
                content = data.get("content")

                # ユーザー要求
                if stype == "USER_INPUT" and content and "[Supervisor" not in content:
                    clean_content = (
                        content.replace("<USER_REQUEST>", "")
                        .replace("</USER_REQUEST>", "")
                        .strip()
                    )
                    clean_content = clean_content.split("<ADDITIONAL_METADATA>")[0].strip()
                    if clean_content and len(recent_events) < 5:
                        recent_events.append(f"【直前のユーザー要求】: {clean_content[:180]}")

                # ファイル編集・作成メタ情報
                elif tc and isinstance(tc, list):
                    for call in tc:
                        name = call.get("name")
                        args = call.get("args", {})
                        if name in ("write_to_file", "replace_file_content"):
                            target_file = args.get("TargetFile", "").strip("\"'")
                            desc = args.get("Description", "").strip("\"'")
                            file_base = Path(target_file).name if target_file else "file"
                            recent_events.append(f"【直前のファイル変更】: {name} ({file_base}) - {desc}")
                        elif name == "run_command":
                            summary = args.get("toolSummary", "").strip("\"'")
                            action = args.get("toolAction", "").strip("\"'")
                            if summary or action:
                                recent_events.append(f"【直前の実行目的】: {summary or action}")

                # エージェントの発言・思考
                elif stype == "PLANNER_RESPONSE" and content and isinstance(content, str):
                    clean_thought = content.strip()
                    if clean_thought and len(recent_events) < 6:
                        recent_events.append(f"【直前のエージェント発言】: {clean_thought[:150]}")

            if recent_events:
                context_lines.extend(reversed(recent_events[:4]))

        except Exception:
            pass

    # 画面バッファフォールバック
    if not context_lines and terminal_buffer:
        clean_buf = re.sub(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])", "", terminal_buffer)
        buf_lines = [l.strip() for l in clean_buf.splitlines() if l.strip()]
        for l in buf_lines[-6:]:
            if not any(k in l for k in ["Supervisor", "RunCommand", "Allow once"]):
                context_lines.append(f"【直前画面コンテキスト】: {l[:120]}")

    if not context_lines:
        return "コンテキスト情報なし（コマンド単体の審査）"

    return "\n\n".join(context_lines)
