"""Cross-platform Pseudo-Terminal (PTY) abstraction adapter for Antigravity Supervisor.

Provides a unified interface for terminal emulation across POSIX (Linux/macOS)
and Windows 10/11 (ConPTY / ctypes), with zero third-party dependencies.
"""

from __future__ import annotations

import os
import queue
import shutil
import struct
import subprocess
import sys
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path


class BaseTerminalSession(ABC):
    """Abstract base class for platform-specific terminal sessions."""

    @abstractmethod
    def start(self, cmd_args: list[str]) -> None:
        """Start the child process within a pseudo-terminal."""
        pass

    @abstractmethod
    def read_child(self, max_bytes: int = 4096) -> bytes:
        """Read output emitted by the child process."""
        pass

    @abstractmethod
    def write_child(self, data: bytes) -> None:
        """Write user/supervisor input to the child process."""
        pass

    @abstractmethod
    def sync_size(self, child_rows: int, cols: int) -> None:
        """Resize the pseudo-terminal window."""
        pass

    @abstractmethod
    def is_alive(self) -> bool:
        """Check if child process is still running."""
        pass

    @abstractmethod
    def close(self) -> None:
        """Clean up process, terminal handles, and restore console state."""
        pass


# ==============================================================================
# POSIX Terminal Session (Linux / macOS)
# ==============================================================================
if sys.platform != "win32":
    import fcntl
    import pty
    import select
    import termios
    import tty

    class PosixTerminalSession(BaseTerminalSession):
        def __init__(self):
            self.master_fd: int | None = None
            self.child_pid: int | None = None
            self.orig_stdin_attrs = None

        def start(self, cmd_args: list[str]) -> None:
            # コマンドの実行パス解決
            exec_bin = shutil.which(cmd_args[0]) or cmd_args[0]
            cmd_args = [exec_bin] + cmd_args[1:]

            self.master_fd, slave_fd = pty.openpty()

            self.child_pid = os.fork()
            if self.child_pid == 0:
                os.close(self.master_fd)
                os.setsid()
                fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
                os.dup2(slave_fd, 0)
                os.dup2(slave_fd, 1)
                os.dup2(slave_fd, 2)
                if slave_fd > 2:
                    os.close(slave_fd)
                try:
                    os.execvp(cmd_args[0], cmd_args)
                except Exception as e:
                    print(f"Error executing {cmd_args[0]}: {e}", file=sys.stderr)
                    os._exit(1)

            os.close(slave_fd)

            # 端末を Raw モードへ移行
            try:
                self.orig_stdin_attrs = termios.tcgetattr(sys.stdin.fileno())
                tty.setraw(sys.stdin.fileno())
            except Exception:
                pass

        def read_child(self, max_bytes: int = 4096) -> bytes:
            if self.master_fd is None:
                return b""
            rlist, _, _ = select.select([self.master_fd], [], [], 0.02)
            if self.master_fd in rlist:
                try:
                    return os.read(self.master_fd, max_bytes)
                except OSError:
                    return b""
            return b""

        def write_child(self, data: bytes) -> None:
            if self.master_fd is not None:
                try:
                    os.write(self.master_fd, data)
                except Exception:
                    pass

        def sync_size(self, child_rows: int, cols: int) -> None:
            if self.master_fd is not None:
                try:
                    child_buf = struct.pack("HHHH", child_rows, cols, 0, 0)
                    fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, child_buf)
                except Exception:
                    pass

        def is_alive(self) -> bool:
            if self.child_pid is None:
                return False
            try:
                pid, _ = os.waitpid(self.child_pid, os.WNOHANG)
                return pid == 0
            except OSError:
                return False

        def close(self) -> None:
            if self.orig_stdin_attrs is not None:
                try:
                    termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self.orig_stdin_attrs)
                except Exception:
                    pass
            if self.master_fd is not None:
                try:
                    os.close(self.master_fd)
                except Exception:
                    pass
                self.master_fd = None

    def read_stdin_nonblocking() -> bytes:
        """Read standard input without blocking on POSIX."""
        rlist, _, _ = select.select([sys.stdin.fileno()], [], [], 0.0)
        if sys.stdin.fileno() in rlist:
            try:
                return os.read(sys.stdin.fileno(), 1024)
            except OSError:
                return b""
        return b""

    def get_terminal_dimensions() -> tuple[int, int]:
        """Get (rows, cols) of the current terminal window."""
        try:
            buf = fcntl.ioctl(sys.stdin.fileno(), termios.TIOCGWINSZ, b"\0" * 8)
            ws_row, ws_col, _, _ = struct.unpack("HHHH", buf)
            return ws_row, ws_col
        except Exception:
            cols, rows = shutil.get_terminal_size((80, 24))
            return rows, cols


# ==============================================================================
# Windows Terminal Session (ConPTY / ctypes / Pipes)
# ==============================================================================
else:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32

    # ConPTY API 定義
    HPCON = wintypes.HANDLE
    COORD = wintypes._COORD

    PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
    EXTENDED_STARTUPINFO_PRESENT = 0x00080000
    ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004

    class WindowsTerminalSession(BaseTerminalSession):
        def __init__(self):
            self.h_pc: HPCON = None
            self.h_pipe_in_write: wintypes.HANDLE = None
            self.h_pipe_out_read: wintypes.HANDLE = None
            self.proc_info = None
            self.output_queue: queue.Queue[bytes] = queue.Queue()
            self.reader_thread: threading.Thread | None = None
            self.running = False
            self.orig_console_mode = wintypes.DWORD()

        def _enable_vt_mode(self):
            """Enable VT100 / ANSI escape sequence processing in Windows Console."""
            h_out = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
            if kernel32.GetConsoleMode(h_out, ctypes.byref(self.orig_console_mode)):
                new_mode = self.orig_console_mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING
                kernel32.SetConsoleMode(h_out, new_mode)

        def start(self, cmd_args: list[str]) -> None:
            self._enable_vt_mode()
            self.running = True

            # コマンドパスの解決 (.cmd / .exe / PATH)
            cmd_name = cmd_args[0]
            resolved_bin = shutil.which(cmd_name)
            if resolved_bin:
                cmd_args[0] = resolved_bin

            cmd_line = subprocess.list2cmdline(cmd_args)

            # パイプ作成
            h_pipe_in_read = wintypes.HANDLE()
            self.h_pipe_in_write = wintypes.HANDLE()
            h_pipe_out_write = wintypes.HANDLE()
            self.h_pipe_out_read = wintypes.HANDLE()

            kernel32.CreatePipe(ctypes.byref(h_pipe_in_read), ctypes.byref(self.h_pipe_in_write), None, 0)
            kernel32.CreatePipe(ctypes.byref(self.h_pipe_out_read), ctypes.byref(h_pipe_out_write), None, 0)

            # 初期サイズ取得
            cols, rows = shutil.get_terminal_size((80, 24))
            size = COORD(cols, rows - 9)

            # ConPTY 作成
            self.h_pc = HPCON()
            res = kernel32.CreatePseudoConsole(size, h_pipe_in_read, h_pipe_out_write, 0, ctypes.byref(self.h_pc))
            if res != 0:
                raise RuntimeError(f"Failed to create Windows PseudoConsole (ConPTY): {res}")

            kernel32.CloseHandle(h_pipe_in_read)
            kernel32.CloseHandle(h_pipe_out_write)

            # プロセス属性リストの初期化
            size_attr = ctypes.c_size_t()
            kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size_attr))
            attr_list = ctypes.create_string_buffer(size_attr.value)
            kernel32.InitializeProcThreadAttributeList(attr_list, 1, 0, ctypes.byref(size_attr))

            kernel32.UpdateProcThreadAttribute(
                attr_list,
                0,
                PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE,
                self.h_pc,
                ctypes.sizeof(self.h_pc),
                None,
                None,
            )

            class STARTUPINFOEXW(ctypes.Structure):
                _fields_ = [
                    ("StartupInfo", subprocess.STARTUPINFO),
                    ("lpAttributeList", ctypes.c_void_p),
                ]

            si = STARTUPINFOEXW()
            si.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEXW)
            si.lpAttributeList = ctypes.cast(attr_list, ctypes.c_void_p)

            pi = subprocess.PROCESS_INFORMATION()

            # 子プロセス (agy) 起動
            success = kernel32.CreateProcessW(
                None,
                cmd_line,
                None,
                None,
                False,
                EXTENDED_STARTUPINFO_PRESENT,
                None,
                None,
                ctypes.byref(si.StartupInfo),
                ctypes.byref(pi),
            )
            if not success:
                err = kernel32.GetLastError()
                raise RuntimeError(f"CreateProcessW failed with error code: {err}")

            self.proc_info = pi

            # 非同期パイプ読込スレッド開始
            self.reader_thread = threading.Thread(target=self._pipe_reader_worker, daemon=True)
            self.reader_thread.start()

        def _pipe_reader_worker(self):
            buf = ctypes.create_string_buffer(4096)
            bytes_read = wintypes.DWORD()
            while self.running and self.h_pipe_out_read:
                success = kernel32.ReadFile(
                    self.h_pipe_out_read,
                    buf,
                    4096,
                    ctypes.byref(bytes_read),
                    None,
                )
                if success and bytes_read.value > 0:
                    data = buf.raw[:bytes_read.value]
                    self.output_queue.put(data)
                else:
                    break

        def read_child(self, max_bytes: int = 4096) -> bytes:
            try:
                return self.output_queue.get_nowait()
            except queue.Empty:
                return b""

        def write_child(self, data: bytes) -> None:
            if self.h_pipe_in_write:
                bytes_written = wintypes.DWORD()
                kernel32.WriteFile(
                    self.h_pipe_in_write,
                    data,
                    len(data),
                    ctypes.byref(bytes_written),
                    None,
                )

        def sync_size(self, child_rows: int, cols: int) -> None:
            if self.h_pc:
                size = COORD(cols, child_rows)
                kernel32.ResizePseudoConsole(self.h_pc, size)

        def is_alive(self) -> bool:
            if not self.proc_info:
                return False
            exit_code = wintypes.DWORD()
            kernel32.GetExitCodeProcess(self.proc_info.hProcess, ctypes.byref(exit_code))
            return exit_code.value == 259  # STILL_ACTIVE

        def close(self) -> None:
            self.running = False
            if self.h_pc:
                kernel32.ClosePseudoConsole(self.h_pc)
                self.h_pc = None
            if self.h_pipe_in_write:
                kernel32.CloseHandle(self.h_pipe_in_write)
                self.h_pipe_in_write = None
            if self.h_pipe_out_read:
                kernel32.CloseHandle(self.h_pipe_out_read)
                self.h_pipe_out_read = None
            # コンソールモード復元
            h_out = kernel32.GetStdHandle(-11)
            kernel32.SetConsoleMode(h_out, self.orig_console_mode)

    import msvcrt

    def read_stdin_nonblocking() -> bytes:
        """Read standard input without blocking on Windows."""
        if msvcrt.kbhit():
            buf = bytearray()
            while msvcrt.kbhit():
                buf.extend(msvcrt.getch())
            return bytes(buf)
        return b""

    def get_terminal_dimensions() -> tuple[int, int]:
        """Get (rows, cols) of the current terminal window on Windows."""
        cols, rows = shutil.get_terminal_size((80, 24))
        return rows, cols


def create_terminal_session() -> BaseTerminalSession:
    """Factory creating OS-appropriate pseudo-terminal session."""
    if sys.platform == "win32":
        return WindowsTerminalSession()
    else:
        return PosixTerminalSession()
