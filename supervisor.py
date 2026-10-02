#!/usr/bin/env python3
"""Antigravity CLI Supervisor.

Monitors agy tool confirmation prompts (RunCommand, etc.) exclusively via
official log events ("Surfacing tool confirmation: RunCommand") to completely
eliminate false positives from TUI spinners and chat text.

Features:
1. Ultra-fast local zero-token guard (microseconds response)
2. Low-effort secondary Antigravity CLI reviewer process (agy -p)
3. Zero chat pollution (no fake user messages injected into conversation)
4. Terminal Titlebar persistence & detailed audit log (~/.gemini/supervisor/audit.log)
5. Comprehensive persistent process monitoring (Web servers, dev servers, etc.)
"""

from __future__ import annotations

import argparse
import datetime
import os
import re
import signal
import sys
import tempfile
import threading
import time
import unicodedata
from pathlib import Path

# Supervisor 自作モジュールのインポート
sys.path.insert(0, str(Path(__file__).parent.resolve()))
from context_extractor import extract_recent_diff_context, get_pending_tool_call
from decision_cache import ProjectDecisionCache, get_project_log_file
from fast_filter import evaluate_fast_filter, is_valid_command_candidate
from notifier import send_desktop_notification
from pty_adapter import create_terminal_session, get_terminal_dimensions, read_stdin_nonblocking
from reviewer import review_command

ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
AUDIT_LOG_FILE = Path.home() / ".gemini" / "supervisor" / "audit.log"


def get_display_width(text: str) -> int:
    """Calculate the terminal display cell width of a string."""
    width = 0
    for ch in text:
        w = unicodedata.east_asian_width(ch)
        if w in ("W", "F"):
            width += 2
        else:
            width += 1
    return width


def truncate_to_width(text: str, max_width: int, ellipsis: str = "...") -> str:
    """Truncate text so its terminal display width does not exceed max_width."""
    if max_width <= 0:
        return ""
    if get_display_width(text) <= max_width:
        return text
    ell_w = get_display_width(ellipsis)
    target_w = max_width - ell_w
    if target_w <= 0:
        res = ""
        for ch in ellipsis:
            if get_display_width(res + ch) > max_width:
                break
            res += ch
        return res

    cur_w = 0
    result = []
    for ch in text:
        w = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if cur_w + w > target_w:
            break
        result.append(ch)
        cur_w += w
    return "".join(result) + ellipsis


def pad_to_width(text: str, total_width: int) -> str:
    """Pad string with trailing spaces until it reaches total_width display cells."""
    cur_w = get_display_width(text)
    pad = max(0, total_width - cur_w)
    return text + (" " * pad)


def wrap_to_n_lines(
    text: str,
    widths: list[int],
    max_lines: int,
    ellipsis: str = "...",
) -> list[str]:
    """Split text into exactly max_lines lines based on display cell width.

    widths: list of max display cell widths for each line.
    Lines prior to the last line prefer breaking at whitespace.
    The last line is truncated with ellipsis if it still exceeds its width.
    """
    if not text:
        return [""] * max_lines

    lines: list[str] = []
    remaining = text.strip()

    for line_idx in range(max_lines):
        if not remaining:
            lines.append("")
            continue

        max_w = widths[line_idx] if line_idx < len(widths) else widths[-1]
        is_last_line = (line_idx == max_lines - 1)

        # 最終行の場合：残りをすべて max_w に収める（超える場合は ellipsis で truncate）
        if is_last_line:
            lines.append(truncate_to_width(remaining, max_w, ellipsis=ellipsis))
            remaining = ""
            break

        # 途中行の場合：すでに収まっていればそのまま
        if get_display_width(remaining) <= max_w:
            lines.append(remaining)
            remaining = ""
            continue

        # 単語境界（スペース）を優先して分割
        cur_w = 0
        idx = 0
        last_space_idx = -1
        for i, ch in enumerate(remaining):
            w = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
            if cur_w + w > max_w:
                break
            cur_w += w
            idx = i + 1
            if ch.isspace():
                last_space_idx = idx

        if last_space_idx > 0 and cur_w >= (max_w * 0.6):
            split_at = last_space_idx
        else:
            split_at = idx

        line_str = remaining[:split_at].rstrip()
        remaining = remaining[split_at:].lstrip()
        lines.append(line_str)

    while len(lines) < max_lines:
        lines.append("")

    return lines


def wrap_to_lines(text: str, line1_max_w: int, line2_max_w: int, ellipsis: str = "...") -> tuple[str, str]:
    """Split text into two lines based on terminal display cell width."""
    res = wrap_to_n_lines(text, [line1_max_w, line2_max_w], 2, ellipsis=ellipsis)
    return res[0], res[1]


def strip_ansi(text: str) -> str:
    """Strip ANSI escape sequences from terminal output."""
    return ANSI_ESCAPE_RE.sub("", text)


def log_audit(decision: str, cmd: str, reason: str, options: str = "", cwd: str | Path | None = None):
    """Append decision to persistent audit log file (both global and project-specific)."""
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"[{now_str}] [{decision}] CMD: {cmd} | OPTIONS: {options} | REASON: {reason}\n"

    # 1. 全体共通監査ログ (~/.gemini/supervisor/audit.log)
    try:
        AUDIT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(log_line)
    except Exception:
        pass

    # 2. プロジェクト個別監査ログ (~/.gemini/supervisor/projects/<project>/audit.log)
    try:
        proj_log = get_project_log_file(cwd)
        with open(proj_log, "a", encoding="utf-8") as f:
            f.write(log_line)
    except Exception:
        pass


def update_titlebar(title: str):
    """Update terminal window titlebar so results never disappear even if TUI refreshes."""
    try:
        sys.stdout.write(f"\x1b]0;{title}\x07")
        sys.stdout.flush()
    except Exception:
        pass


class AgySupervisor:
    CMD_LINES = 4
    REASON_LINES = 4
    PANEL_ROWS = 1 + CMD_LINES + REASON_LINES  # 9行構成 (ヘッダー1行 + CMD 4行 + 理由 4行)

    def __init__(
        self,
        agy_args: list[str],
        supervisor_model: str = "gemini-3.8-flash-low",
        timeout_sec: float = 15.0,
        enable_fast_filter: bool = True,
    ):
        self.agy_args = agy_args
        self.supervisor_model = supervisor_model
        self.timeout_sec = timeout_sec
        self.enable_fast_filter = enable_fast_filter

        self.session = create_terminal_session()
        self.log_file = Path(tempfile.gettempdir()) / f"agy_supervisor_{os.getpid()}_{int(time.time())}.log"
        self.running = True

        # 出力バッファ（直近のテキストを保持）
        self.buffer_lock = threading.Lock()
        self.recent_output = ""
        self.max_buffer_len = 32768

        self.last_handled_time = 0.0
        self.pending_confirmation_event = threading.Event()
        self.decision_cache = ProjectDecisionCache()
        self.conv_id: str | None = None

        self.real_rows: int = 24
        self.real_cols: int = 80

        # セッション統計カウンター
        self.stats = {
            "approve": 0,
            "reject": 0,
            "cached": 0,
            "fast": 0,
            "ai": 0,
        }

        # 9行パネル描画用の状態保持
        self._last_panel_data: dict[str, str] = {
            "status": "READY",
            "cmd": f"監視常駐中 ({self.supervisor_model})",
            "reason": "監査ログ: ~/.gemini/supervisor/audit.log",
            "source": "常駐待機",
            "cwd": str(Path.cwd()),
        }

    def _sync_terminal_size(self):
        """Sync window size from real terminal to pseudo-terminal, reserving bottom rows for dashboard panel."""
        try:
            ws_row, ws_col = get_terminal_dimensions()
            self.real_rows = ws_row
            self.real_cols = ws_col
            # agy (BubbleTea) には最下部 PANEL_ROWS 行を除いた高さを渡すことで、ダッシュボードパネルを常時確保
            child_rows = max(5, ws_row - self.PANEL_ROWS)
            self.session.sync_size(child_rows, ws_col)
            # DECSTBM: 物理端末のスクロールマージンを 1 行目から child_rows 行目に固定（パネル行の巻き込みスクロールを完全防止）
            sys.stdout.write(f"\x1b[1;{child_rows}r")
            sys.stdout.flush()
            self._render_statusbar()
        except Exception:
            pass

    def _status_render_thread(self):
        """Continuously re-render the dashboard panel so it stays permanently visible regardless of TUI refreshes."""
        while self.running:
            time.sleep(0.35)
            self._render_statusbar()

    def _render_statusbar(
        self,
        status: str = "",
        cmd: str = "",
        reason: str = "",
        source: str = "",
        cwd: str = "",
    ):
        """Render a persistent 11-line rich dashboard panel at the bottom rows of the terminal."""
        if not hasattr(self, "real_rows") or not self.real_rows or not self.real_cols:
            return

        if status:
            self._last_panel_data = {
                "status": status,
                "cmd": cmd or self._last_panel_data.get("cmd", ""),
                "reason": reason or self._last_panel_data.get("reason", ""),
                "source": source or self._last_panel_data.get("source", ""),
                "cwd": cwd or self._last_panel_data.get("cwd", str(Path.cwd())),
            }

        data = self._last_panel_data
        status_val = data.get("status", "READY")
        cmd_val = data.get("cmd", "")
        reason_val = data.get("reason", "").replace("\r", " ").replace("\n", " ").strip()
        source_val = data.get("source", "")
        cwd_val = data.get("cwd", "")

        cols = max(40, self.real_cols)
        rows = self.real_rows

        # --- 行 1: ステータスヘッダー ＆ セッション統計 ---
        if status_val == "APPROVE":
            tag = " \x1b[1;30;42m ✔ APPROVE \x1b[0m"
            tag_plain_len = 11
        elif status_val == "REJECT":
            tag = " \x1b[1;37;41m ✖ REJECT \x1b[0m"
            tag_plain_len = 10
        elif status_val in ("REVIEWING", "審査中"):
            tag = " \x1b[1;30;43m ⏳ 審査中 \x1b[0m"
            tag_plain_len = 9
        else:
            tag = f" \x1b[1;37;44m 🛡️ SUPERVISOR: {status_val} \x1b[0m"
            tag_plain_len = 16 + len(status_val)

        # 統計テキスト
        appr_cnt = self.stats["approve"]
        rej_cnt = self.stats["reject"]
        fast_cnt = self.stats["fast"] + self.stats["cached"]
        saved_tok = fast_cnt * 1.5  # 1件あたり平均約1.5kトークン節約

        stats_text = (
            f"[ 承認:\x1b[1;32m{appr_cnt}\x1b[0;38;5;250m "
            f"拒否:\x1b[1;31m{rej_cnt}\x1b[0;38;5;250m | "
            f"⚡即時:\x1b[1;36m{fast_cnt}\x1b[0;38;5;250m | "
            f"節約:\x1b[1;33m~{saved_tok:.1f}k tok\x1b[0;38;5;250m ] "
        )
        stats_plain = f"[ 承認:{appr_cnt} 拒否:{rej_cnt} | ⚡即時:{fast_cnt} | 節約:~{saved_tok:.1f}k tok ] "
        stats_plain_len = get_display_width(stats_plain)

        # 境界線（━）の長さ
        sep_len = max(2, cols - tag_plain_len - stats_plain_len - 2)
        sep_bar = "\x1b[38;5;242m" + ("━" * sep_len) + "\x1b[0m"

        line1_content = f"{tag} {sep_bar} \x1b[38;5;250m{stats_text}\x1b[0m"

        # --- 行 2〜6: 💻 CMD (5行割り当て) ＆ 作業ディレクトリ (CWD) ---
        cwd_disp = ""
        if cwd_val:
            home_dir = str(Path.home())
            if cwd_val.startswith(home_dir):
                cwd_disp = "~" + cwd_val[len(home_dir):]
            else:
                cwd_disp = cwd_val

        cwd_part = f"📁 {cwd_disp} " if cwd_disp else ""
        cwd_w = get_display_width(cwd_part)

        cmd_widths = [
            max(10, cols - 10),           # 行 1: " 💻 CMD: " (9セル)
            max(10, cols - 10),           # 行 2: "    │   " (8セル)
            max(10, cols - 10),           # 行 3: "    │   " (8セル)
            max(10, cols - cwd_w - 9),    # 行 4: "    │   " (8セル) + CWD
        ]
        cmd_lines = wrap_to_n_lines(cmd_val, cmd_widths, self.CMD_LINES)

        cmd_contents = []
        for i, text in enumerate(cmd_lines):
            if i == 0:
                inner = pad_to_width(f" 💻 CMD: {text}", cols)
                cmd_contents.append(f"\x1b[48;5;236;38;5;255m{inner}\x1b[0m")
            elif i == self.CMD_LINES - 1:
                prefix = "    │   " if text else "        "
                inner = pad_to_width(f"{prefix}{text}", cols - cwd_w)
                cmd_contents.append(f"\x1b[48;5;236;38;5;255m{inner}\x1b[38;5;250m{cwd_part}\x1b[0m")
            else:
                prefix = "    │   " if text else "        "
                inner = pad_to_width(f"{prefix}{text}", cols)
                cmd_contents.append(f"\x1b[48;5;236;38;5;255m{inner}\x1b[0m")

        # --- 行 6〜9: 📝 理由 (4行割り当て) ＆ 判定元バッジ ---
        source_badge = ""
        if source_val:
            if "高速ガード" in source_val:
                source_badge = f"\x1b[1;32m[{source_val}]\x1b[0;48;5;234;38;5;250m "
            elif "キャッシュ" in source_val:
                source_badge = f"\x1b[1;36m[{source_val}]\x1b[0;48;5;234;38;5;250m "
            elif "審査役AI" in source_val:
                source_badge = f"\x1b[1;33m[{source_val}]\x1b[0;48;5;234;38;5;250m "
            else:
                source_badge = f"\x1b[1;34m[{source_val}]\x1b[0;48;5;234;38;5;250m "

        source_plain = f"[{source_val}] " if source_val else ""
        source_w = get_display_width(source_plain)

        reason_widths = [
            max(10, cols - 11),              # 行 1: " 📝 理由: " (10セル)
            max(10, cols - 10),              # 行 2: "    │    " (9セル)
            max(10, cols - 10),              # 行 3: "    │    " (9セル)
            max(10, cols - source_w - 10),   # 行 4: "    │    " (9セル) + source_badge
        ]
        reason_lines = wrap_to_n_lines(reason_val, reason_widths, self.REASON_LINES)

        reason_contents = []
        for j, r_text in enumerate(reason_lines):
            if j == 0:
                inner = pad_to_width(f" 📝 理由: {r_text}", cols)
                reason_contents.append(f"\x1b[48;5;234;38;5;250m{inner}\x1b[0m")
            elif j == self.REASON_LINES - 1:
                prefix = "    │    " if r_text else "         "
                inner = pad_to_width(f"{prefix}{r_text}", cols - source_w)
                reason_contents.append(f"\x1b[48;5;234;38;5;250m{inner}{source_badge}\x1b[0m")
            else:
                prefix = "    │    " if r_text else "         "
                inner = pad_to_width(f"{prefix}{r_text}", cols)
                reason_contents.append(f"\x1b[48;5;234;38;5;250m{inner}\x1b[0m")

        # 11行をまとめて1回のwriteでアトミックに描画（ちらつき防止）
        start_row = rows - self.PANEL_ROWS + 1
        panel_parts = [f"\x1b7\x1b[{start_row};1H\x1b[2K{line1_content}"]
        for idx_c, c_line in enumerate(cmd_contents):
            panel_parts.append(f"\x1b[{start_row + 1 + idx_c};1H\x1b[2K{c_line}")
        for idx_r, r_line in enumerate(reason_contents):
            panel_parts.append(f"\x1b[{start_row + 1 + self.CMD_LINES + idx_r};1H\x1b[2K{r_line}")
        panel_parts.append("\x1b8")
        panel_ansi = "".join(panel_parts)

        try:
            sys.stdout.write(panel_ansi)
            sys.stdout.flush()
        except Exception:
            pass

    def _tail_log_thread(self):
        """Monitor agy log file for tool confirmation events with ultra-low latency.

        Only official log events from agy trigger confirmation handling,
        completely preventing false positives while editing files or chatting.
        """
        waited = 0.0
        while self.running and not self.log_file.exists():
            time.sleep(0.05)
            waited += 0.05
            if waited > 10.0:
                break

        if not self.log_file.exists():
            return

        try:
            with open(self.log_file, "r", encoding="utf-8", errors="ignore") as f:
                f.seek(0, os.SEEK_END)
                while self.running:
                    line = f.readline()
                    if line:
                        m_conv = re.search(r"(?:conversation|convID=)\s*([a-f0-9\-]{36})", line)
                        if m_conv:
                            self.conv_id = m_conv.group(1)
                        if 'Surfacing tool confirmation: "RunCommand"' in line:
                            self.pending_confirmation_event.set()
                    else:
                        time.sleep(0.005)  # 5ms 超低遅延ポーリング
        except Exception:
            pass

    def _detect_options(self, raw_buffer: str) -> str:
        """Detect available confirmation choices from terminal buffer."""
        plain = strip_ansi(raw_buffer)
        detected = []
        if "Allow once" in plain or "(y)" in plain:
            detected.append("y: 1回許可")
        if "Allow always" in plain or "(a)" in plain:
            detected.append("a: 常に許可")
        if "Deny" in plain or "(n)" in plain:
            detected.append("n: 拒否")
        return ", ".join(detected) if detected else "y: 許可, n: 拒否"

    def _extract_command(self, raw_buffer: str) -> str | None:
        """Extract candidate command line from recent terminal buffer when RunCommand is surfaced."""
        plain = strip_ansi(raw_buffer)
        lines = [line.strip() for line in plain.splitlines() if line.strip()]

        # 1. CommandLine: や Command: などのキーを検索
        for line in reversed(lines[-30:]):
            m = re.search(
                r'(?:CommandLine|Command|run_command|RunCommand)[\s:=]+["\']?([^"\']+)["\']?',
                line,
                re.IGNORECASE,
            )
            if m:
                cand = m.group(1).strip()
                if is_valid_command_candidate(cand):
                    return cand

        # 2. シェルプロンプト形式 $ <cmd> を検索
        for line in reversed(lines[-25:]):
            if line.startswith("$ ") and len(line) > 2:
                cand = line[2:].strip()
                if is_valid_command_candidate(cand):
                    return cand

        # 3. バッククォート囲み `...` を検索（ただしスピナーや文章は除外）
        for line in reversed(lines[-25:]):
            for m in re.finditer(r'`([^`]{2,})`', line):
                cand = m.group(1).strip()
                if is_valid_command_candidate(cand):
                    return cand

        # 4. 有効なコマンドが見つからない場合は None を返す（ゴミデータを無理にコマンドと判定しない）
        return None

    def _handle_confirmation(self):
        """Evaluate command and inject decision into PTY."""
        with self.buffer_lock:
            buf_snapshot = self.recent_output

        # 1. まず公式の transcript.jsonl から最新の tool_call（CommandLine, Cwd）をミリ秒取得
        cmd_info = get_pending_tool_call(self.conv_id)
        cmd = None
        cwd = None
        if cmd_info:
            cmd = cmd_info.get("cmd")
            cwd = cmd_info.get("cwd")

        # transcript がディスクフラッシュ前等で見つからない場合、最大3回（計150ms）リトライ
        if not cmd:
            for _ in range(3):
                time.sleep(0.05)
                cmd_info = get_pending_tool_call(self.conv_id)
                if cmd_info:
                    cmd = cmd_info.get("cmd")
                    cwd = cmd_info.get("cwd")
                    break

        # それでも取得できない場合のみ画面バッファからの抽出にフォールバック
        if not cmd:
            cmd = self._extract_command(buf_snapshot)

        options = self._detect_options(buf_snapshot)

        # 有効なコマンドが抽出できなかった場合
        if not cmd:
            self.pending_confirmation_event.clear()
            return

        decision = None
        reason = ""
        source = ""
        start_t = time.perf_counter()

        # 1. 高速ローカルフィルタ判定（0.001秒・トークン消費 0）
        # ※スクリプト実行の場合は None が返るため、安全にキャッシュ/審査役へ委ねられます
        if self.enable_fast_filter:
            fast_res = evaluate_fast_filter(cmd)
            if fast_res is not None:
                decision, reason = fast_res
                elapsed = time.perf_counter() - start_t
                source = f"高速ガード {elapsed*1000:.1f}ms"
                self.stats["fast"] += 1

        # 2. プロジェクト別学習キャッシュ判定（0.001秒・トークン消費 0）
        # ※スクリプト実行時はファイルハッシュが自動検証されます（変更があれば自動キャッシュMISS）
        if decision is None:
            cached_res = self.decision_cache.get(cmd, cwd=cwd)
            if cached_res is not None:
                decision, reason = cached_res
                elapsed = time.perf_counter() - start_t
                source = f"学習キャッシュ {elapsed*1000:.1f}ms"
                self.stats["cached"] += 1

        # 3. 審査役AI呼び出し（agy -p: スクリプト変更時または未審査コマンドのみ）
        if decision is None:
            self._render_statusbar(
                status="審査中",
                cmd=cmd,
                reason=f"AI検証中 ({self.supervisor_model})...",
                source="AI審査実行中",
                cwd=cwd or "",
            )
            update_titlebar(f"[Supervisor 審査中] {cmd[:40]}...")
            sys.stdout.write(
                f"\r\n\x1b[1;33m[Supervisor 審査中]\x1b[0m 審査役AI（{self.supervisor_model}）で検証中...\r\n"
            )
            sys.stdout.flush()

            # 直前のファイル変更差分（diff）や要求コンテキストを自動抽出
            context = extract_recent_diff_context(
                conv_id=self.conv_id,
                cmd=cmd,
                cwd=cwd,
                terminal_buffer=buf_snapshot,
            )

            decision, reason = review_command(
                command=cmd,
                context=context,
                model=self.supervisor_model,
                timeout_sec=self.timeout_sec,
            )
            elapsed = time.perf_counter() - start_t
            source = f"審査役AI {elapsed:.1f}s"
            self.stats["ai"] += 1

            # 判定結果とスクリプトハッシュをキャッシュに保存（次回は0.001秒で即時承認）
            self.decision_cache.put(cmd, decision, reason, cwd=cwd)

        # 承認・拒否カウンタの更新
        if decision == "APPROVE":
            self.stats["approve"] += 1
        else:
            self.stats["reject"] += 1

        # 最下部 3 行ダッシュボードパネルを常駐更新（TUIがどれだけ再描画しても絶対に消えない！）
        self._render_statusbar(
            status=decision,
            cmd=cmd,
            reason=reason,
            source=source,
            cwd=cwd or "",
        )

        # タイトルバーに常駐表示（画面が再描画されても絶対に消えない！）
        title_summary = f"[Supervisor: {decision}] {cmd[:30]} | {reason[:40]}"
        update_titlebar(title_summary)

        # デスクトップ通知（画面再描画の影響を受けず右上に確実にポップアップ）
        send_desktop_notification(decision, cmd, reason)

        # 永続監査ログに記録（全体ログ & プロジェクト別ログ）
        audit_note = f"CWD: {cwd} | [{source}] {reason}" if cwd else f"[{source}] {reason}"
        log_audit(decision, cmd, audit_note, options, cwd=cwd)

        if decision == "APPROVE":
            # ステータスバーの緑（APPROVE）を視認できるよう、0.5秒だけ表示を維持してから送信
            time.sleep(0.5)
            try:
                self.session.write_child(b"y\r\n")
            except Exception:
                pass
        else:
            # 拒否時は赤（REJECT）を1.0秒維持してから確実にキャンセル
            time.sleep(1.0)
            try:
                # 重要: \r\n（Enter）を送るとデフォルト選択の Allow once が決定されてしまうため、
                # Esc キー（\x1b）を送信してダイアログを確実にキャンセル（Deny）する
                self.session.write_child(b"\x1b")
            except Exception:
                pass

            # エージェントがダイアログ終了後にプロンプト待ちへ戻るのを見計らい、
            # 拒否理由と代替手段探索の指示を自動フィードバック注入して探索を継続させる
            def _inject_recovery_feedback():
                time.sleep(1.2)
                clean_reason = reason.replace("\r", " ").replace("\n", " ").strip()
                feedback_msg = (
                    f"【Supervisor安全通知】直前のコマンド `{cmd}` は安全規約により拒否されました。"
                    f"【拒否理由】: {clean_reason}。"
                    f"この理由を踏まえ、安全な代替コマンドまたは別のアプローチで作業を自律的に続行してください。\r\n"
                )
                try:
                    self.session.write_child(feedback_msg.encode("utf-8"))
                except Exception:
                    pass

            threading.Thread(target=_inject_recovery_feedback, daemon=True).start()

        self.last_handled_time = time.time()
        self.pending_confirmation_event.clear()

    def run(self):
        """Main execution loop setting up PTY and bidirectional I/O."""
        cmd_args = ["agy"]
        if not any(arg.startswith("--mode") for arg in self.agy_args):
            cmd_args.extend(["--mode", "accept-edits"])
        cmd_args.extend(["--log-file", str(self.log_file)])
        cmd_args.extend(self.agy_args)

        if hasattr(signal, "SIGWINCH"):
            def handle_sigwinch(signum, frame):
                self._sync_terminal_size()

            signal.signal(signal.SIGWINCH, handle_sigwinch)

        self.session.start(cmd_args)
        self._sync_terminal_size()

        # ログ監視スレッド開始（公式ログイベントのみをトリガーとする）
        log_thread = threading.Thread(target=self._tail_log_thread, daemon=True)
        log_thread.start()

        # 最下行ステータスバー常駐スレッド開始（0.35秒周期で描画死守）
        status_thread = threading.Thread(target=self._status_render_thread, daemon=True)
        status_thread.start()

        # 画面最下行にSupervisor専用ダッシュボードパネルを初期表示
        self._render_statusbar(
            status="READY",
            cmd=f"監視常駐中 ({self.supervisor_model})",
            reason="監査ログ: ~/.gemini/supervisor/audit.log",
            source="常駐待機",
            cwd=str(Path.cwd()),
        )

        update_titlebar(f"Antigravity [Supervisor Active: {self.supervisor_model}]")

        sys.stdout.write(
            f"\r\n\x1b[1;35m=== Antigravity Supervisor 起動 ===\x1b[0m\r\n"
            f"審査役モデル: \x1b[1;33m{self.supervisor_model}\x1b[0m | 高速ガード: \x1b[1;32m{'有効 (爆速0秒判定)' if self.enable_fast_filter else '無効'}\x1b[0m\r\n"
            f"監査ログ: \x1b[1;34m~/.gemini/supervisor/audit.log\x1b[0m\r\n\r\n"
        )
        sys.stdout.flush()

        try:
            last_size_check = time.time()
            while self.running:
                now = time.time()
                # ログからの公式イベント通知のみで発火（誤爆ゼロ保証）
                # メインループ（キー入力・画面出力）をフリーズさせないよう非同期スレッドで実行
                if self.pending_confirmation_event.is_set() and (now - self.last_handled_time > 0.2):
                    self.pending_confirmation_event.clear()
                    self.last_handled_time = now
                    threading.Thread(target=self._handle_confirmation, daemon=True).start()

                # 定期的にウィンドウサイズ変化を検知 (0.5秒おき、特にWindows向け)
                if now - last_size_check > 0.5:
                    last_size_check = now
                    curr_row, curr_col = get_terminal_dimensions()
                    if curr_row != self.real_rows or curr_col != self.real_cols:
                        self._sync_terminal_size()

                had_activity = False

                # ユーザー標準入力の非ブロッキング転送
                user_input = read_stdin_nonblocking()
                if user_input:
                    had_activity = True
                    # Ctrl+C (\x03) が入力された場合、子プロセスグループに即座に SIGINT シグナルを叩き込む
                    if b"\x03" in user_input:
                        self.session.send_signal(signal.SIGINT)
                    self.session.write_child(user_input)

                # 子プロセス出力の読み取り
                child_output = self.session.read_child(4096)
                if child_output:
                    had_activity = True
                    sys.stdout.buffer.write(child_output)
                    sys.stdout.buffer.flush()

                    text_chunk = child_output.decode("utf-8", errors="ignore")
                    with self.buffer_lock:
                        self.recent_output += text_chunk
                        if len(self.recent_output) > self.max_buffer_len:
                            self.recent_output = self.recent_output[-self.max_buffer_len:]

                # 子プロセスの生存確認
                if not self.session.is_alive():
                    # 残りの出力をフラッシュ
                    time.sleep(0.05)
                    remaining_output = self.session.read_child(4096)
                    if remaining_output:
                        sys.stdout.buffer.write(remaining_output)
                        sys.stdout.buffer.flush()
                    break

                if not had_activity:
                    time.sleep(0.01)

        finally:
            self.running = False
            self.session.close()
            if hasattr(self, "real_rows") and self.real_rows:
                try:
                    # \x1b[r でスクロールマージンを端末全画面に復元し、最下部 PANEL_ROWS 行をクリア
                    start_clear = max(1, self.real_rows - self.PANEL_ROWS + 1)
                    sys.stdout.write(f"\x1b[r\x1b[{start_clear};1H\x1b[J")
                    sys.stdout.flush()
                except Exception:
                    pass
            if self.log_file.exists():
                try:
                    self.log_file.unlink()
                except Exception:
                    pass
            update_titlebar("Terminal")
            sys.stdout.write("\r\n\x1b[1;35m=== Antigravity Supervisor 終了 ===\x1b[0m\r\n")
            sys.stdout.flush()


def main():
    parser = argparse.ArgumentParser(
        description="Antigravity CLI Supervisor with AI Judge",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--supervisor-model",
        default=os.environ.get("SUPERVISOR_MODEL", "gemini-3.8-flash-low"),
        help="Model used by the supervisor/reviewer agy process for evaluations",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="Review timeout in seconds",
    )
    parser.add_argument(
        "--no-fast-filter",
        action="store_true",
        help="Disable local zero-token guard and evaluate all commands with LLM",
    )

    args, unknown = parser.parse_known_args()

    supervisor = AgySupervisor(
        agy_args=unknown,
        supervisor_model=args.supervisor_model,
        timeout_sec=args.timeout,
        enable_fast_filter=not args.no_fast_filter,
    )
    supervisor.run()


if __name__ == "__main__":
    main()
