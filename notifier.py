"""Cross-platform desktop notification module for Antigravity Supervisor.

Supports Linux (notify-send), macOS (osascript), and Windows 10/11 (PowerShell Toast),
falling back gracefully without external package dependencies.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys


def send_desktop_notification(decision: str, cmd: str, reason: str):
    """Send OS-native desktop notification asynchronously without blocking."""
    title = f"[Supervisor: {decision}]"
    clean_body = f"{cmd[:60]}\n{reason[:100]}".replace('"', "'").replace("\r", " ").replace("\n", " ").strip()

    try:
        if sys.platform == "linux":
            if shutil.which("notify-send"):
                icon = "dialog-ok" if decision == "APPROVE" else "dialog-error"
                subprocess.Popen(
                    ["notify-send", "-a", "Antigravity Supervisor", "-i", icon, "-t", "4000", title, clean_body],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        elif sys.platform == "darwin":
            # macOS AppleScript notification
            osa_script = f'display notification "{clean_body}" with title "{title}"'
            subprocess.Popen(
                ["osascript", "-e", osa_script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        elif sys.platform == "win32":
            # Windows PowerShell Toast Notification (Windows 10/11 native)
            ps_cmd = (
                f'[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null; '
                f'$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02); '
                f'$textNodes = $template.GetElementsByTagName("text"); '
                f'$textNodes.Item(0).AppendChild($template.CreateTextNode("{title}")) > $null; '
                f'$textNodes.Item(1).AppendChild($template.CreateTextNode("{clean_body}")) > $null; '
                f'$toast = [Windows.UI.Notifications.ToastNotification]::new($template); '
                f'[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Antigravity Supervisor").Show($toast);'
            )
            subprocess.Popen(
                ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_cmd],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except Exception:
        pass
