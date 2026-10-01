"""Project-level Decision Cache for Antigravity Supervisor with Script Hash Validation.

Records approved and rejected commands per project directory.
For script executions (e.g. python, bash), tracks the SHA-256 hash of the script file.
If the script is modified, the cache key automatically invalidates, triggering a re-review.
If the script is unchanged, subsequent executions resolve in microseconds without token consumption.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
from datetime import datetime
from pathlib import Path

CACHE_DIR = Path.home() / ".gemini" / "supervisor" / "cache"
PROJECTS_DIR = Path.home() / ".gemini" / "supervisor" / "projects"


def get_project_identifier(project_dir: str | Path | None = None) -> tuple[str, str, str]:
    """Return (sanitized_name, path_hash, resolved_project_path) for project."""
    if project_dir is None:
        project_dir = os.getcwd()
    proj_path = str(Path(project_dir).resolve())
    path_hash = hashlib.sha256(proj_path.encode("utf-8")).hexdigest()[:12]
    sanitized_name = re.sub(r"[^a-zA-Z0-9_-]", "_", proj_path.strip("/"))
    if len(sanitized_name) > 30:
        sanitized_name = sanitized_name[-30:]
    return sanitized_name, path_hash, proj_path


def get_project_log_file(project_dir: str | Path | None = None) -> Path:
    """Return path to project-specific audit.log file."""
    sanitized_name, path_hash, _ = get_project_identifier(project_dir)
    proj_dir = PROJECTS_DIR / f"{sanitized_name}_{path_hash}"
    proj_dir.mkdir(parents=True, exist_ok=True)
    return proj_dir / "audit.log"


def extract_script_file(cmd: str, cwd: str | Path | None = None) -> Path | None:
    """Extract path to script file executed by python, bash, sh, or direct execution."""
    if not cmd:
        return None
    try:
        tokens = shlex.split(cmd)
    except Exception:
        tokens = cmd.split()
    if not tokens:
        return None

    base_name = Path(tokens[0]).name
    candidate: str | None = None

    if base_name in ("python", "python3", "bash", "sh", "zsh", "node"):
        # インタプリタの引数（-u, -v等）をスキップし、-c / -m は除外
        i = 1
        while i < len(tokens):
            tok = tokens[i]
            if tok in ("-c", "-m"):
                return None
            if tok.startswith("-"):
                i += 1
                continue
            candidate = tok
            break
    elif base_name.endswith((".py", ".sh", ".bash", ".js")):
        candidate = tokens[0]

    if candidate:
        clean_cand = candidate.strip("\"'")
        p = Path(clean_cand)
        if not p.is_absolute():
            base_dir = Path(cwd).resolve() if cwd else Path.cwd().resolve()
            p = (base_dir / p).resolve()
        if p.exists() and p.is_file():
            return p
    return None


def compute_file_hash(file_path: Path) -> str | None:
    """Compute short SHA-256 hash of file content."""
    try:
        if file_path.exists() and file_path.is_file():
            h = hashlib.sha256()
            with open(file_path, "rb") as f:
                while chunk := f.read(65536):
                    h.update(chunk)
            return h.hexdigest()[:16]
    except Exception:
        pass
    return None


class ProjectDecisionCache:
    def __init__(self, project_dir: str | Path | None = None):
        sanitized_name, path_hash, proj_path = get_project_identifier(project_dir)
        self.project_path = proj_path

        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.cache_file = CACHE_DIR / f"{sanitized_name}_{path_hash}.json"
        self.project_log_file = get_project_log_file(project_dir)
        self._cache_data: dict = self._load()

    def log_decision(self, decision: str, cmd: str, reason: str, options: str = ""):
        """Append decision to project-specific audit log file."""
        try:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(self.project_log_file, "a", encoding="utf-8") as f:
                f.write(f"[{now_str}] [{decision}] CMD: {cmd} | OPTIONS: {options} | REASON: {reason}\n")
        except Exception:
            pass

    def _load(self) -> dict:
        """Load cache from disk."""
        if self.cache_file.exists():
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "project_path": self.project_path,
            "created_at": datetime.now().isoformat(),
            "decisions": {},
        }

    def _save(self):
        """Persist cache to disk atomically."""
        try:
            temp_file = self.cache_file.with_suffix(".tmp")
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(self._cache_data, f, ensure_ascii=False, indent=2)
            temp_file.replace(self.cache_file)
        except Exception:
            pass

    def _make_key(self, cmd: str, cwd: str | Path | None = None) -> tuple[str, str | None, str | None]:
        """Generate cache key factoring in script content SHA-256 hash if applicable."""
        clean_cmd = cmd.strip()
        script_file = extract_script_file(clean_cmd, cwd)
        if script_file:
            fhash = compute_file_hash(script_file)
            if fhash:
                key = f"{clean_cmd}##fhash:{script_file.name}:{fhash}"
                return key, script_file.name, fhash
        return clean_cmd, None, None

    def get(self, cmd: str, cwd: str | Path | None = None) -> tuple[str, str] | None:
        """Look up command in cache.
        If command runs a script file, ensures the script SHA-256 hash matches.
        Returns:
            (decision, reason) if cached and valid, or None.
        """
        key, script_name, fhash = self._make_key(cmd, cwd)
        entry = self._cache_data.get("decisions", {}).get(key)
        if entry:
            entry["hit_count"] = entry.get("hit_count", 0) + 1
            entry["last_accessed"] = datetime.now().isoformat()
            self._save()
            decision = entry.get("decision", "REJECT")
            reason = entry.get("reason", "過去の審査結果（プロジェクトキャッシュ）")
            hash_info = f" [スクリプト同一性確認済: {script_name} ({fhash})]" if fhash else ""
            return decision, f"【プロジェクト学習キャッシュ】{reason}{hash_info}（ヒット回数: {entry['hit_count']}）"
        return None

    def put(self, cmd: str, decision: str, reason: str, cwd: str | Path | None = None):
        """Record decision in cache with associated script file hash."""
        clean_cmd = cmd.strip()
        if not clean_cmd:
            return
        key, script_name, fhash = self._make_key(cmd, cwd)
        if "decisions" not in self._cache_data:
            self._cache_data["decisions"] = {}

        self._cache_data["decisions"][key] = {
            "decision": decision,
            "reason": reason,
            "script_name": script_name,
            "file_hash": fhash,
            "cached_at": datetime.now().isoformat(),
            "hit_count": 1,
        }
        self._save()
