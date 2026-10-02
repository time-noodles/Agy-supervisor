"""Comprehensive fast local rule filter for instant decision without consuming LLM tokens.
Evaluates commands deterministically against all 10 security & operational rules.

Includes AST inspection for `python -c` and robust pipeline validation.
"""

from __future__ import annotations

import ast
import re
import shlex

# バイナリ・画像拡張子（cat/view/edit禁止）
BINARY_EXTENSIONS = (
    ".tif",
    ".tiff",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".npy",
    ".npz",
    ".h5",
    ".hdf5",
    ".pkl",
    ".pickle",
    ".so",
    ".tar",
    ".gz",
    ".zip",
    ".parquet",
    ".feather",
)

# 明確に危険なパターン（即時 REJECT）
DANGEROUS_PATTERNS = [
    (r"\bsudo\b", "特権昇格（sudo）は規約により禁止されています。"),
    (r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f*|-rf)\s+([/~]|\*|\.\.)", "広範囲・破壊的な一括削除（rm -rf）は禁止されています。"),
    (r"\bgit\s+reset\s+--hard\b", "コミット履歴の不可逆な巻き戻し（git reset --hard）は禁止されています。"),
    (r"\bgit\s+clean\s+-[a-zA-Z]*f\b", "未追跡ファイルの強制削除（git clean -f）は禁止されています。"),
    (r"\b(env|printenv)\b", "環境変数・シークレット漏洩防止のため、全環境変数の一覧表示は禁止されています。"),
    (r"\bcat\s+.*~/\.ssh/", "SSH鍵などのクレデンシャル直接表示は禁止されています。"),
    (r"\bcat\s+.*\.env\b", ".env ファイルの直接表示は情報漏洩防止のため禁止されています。"),
    (r"\b(shutdown|reboot|poweroff|init\s+0)\b", "システムの再起動・電源断コマンドは禁止されています。"),
    (r"\bchmod\s+(-R\s+)?777\b", "危険なパーミッション全開放（chmod 777）は禁止されています。"),
    # 永続プロセスの監視強化（フォアグラウンド直接実行の禁止）
    (r"\b(tail\s+-f|watch\s+|sleep\s+[1-9]\d{3,})\b", "終了しない永続プロセスのフォアグラウンド直接実行はフリーズ防止のため禁止されています。"),
    (r"\bwhile\s+(?:true\b|:\s*;|:\s+do|\[\s*1\s*\]|\[\[\s*1\s*\]\])", "終了しない無限ループ（while true 等）の直接実行はフリーズ防止のため禁止されています。"),
    (r"\bfor\s*\(\s*\(\s*;\s*;\s*\)\s*\)", "終了しない無限ループ（for (;;) 等）の直接実行はフリーズ防止のため禁止されています。"),
    (r"(?:^|[|;&]\s*)yes(?:\s+.*)?$", "無限ストリーム生成コマンド（yes）の直接実行はフリーズ防止のため禁止されています。"),
    (r"\bpython[0-9.]*\s+(-m\s+)?http\.server\b", "Webサーバーのフォアグラウンド直接実行は禁止されています（バックグラウンド実行 & が必要です）。"),
    (r"\b(npm|yarn|pnpm|bun)\s+(start|dev|serve)\b", "開発サーバーのフォアグラウンド直接実行は禁止されています（バックグラウンド実行 & が必要です）。"),
    (r"\b(streamlit\s+run|flask\s+run|uvicorn|gunicorn)\b", "Webフレームワークのフォアグラウンド直接実行は禁止されています（バックグラウンド実行 & が必要です）。"),
    (r"\b(top|htop|glances)\b", "対話型監視ツールの直接実行はCLIセッションのスタックを引き起こすため禁止されています。"),
    (r"\b(mkfs|dd\s+if=.*of=/dev/)\b", "ディスクフォーマット等の破壊的操作は禁止されています。"),
    (r">\s*(/etc/|/boot/|/usr/|/bin/|/sbin/)", "システム重要領域への直接書き込みは禁止されています。"),
]

# 許可されている一般的な安全ベースコマンド
SAFE_BASE_COMMANDS = {
    "pwd",
    "whoami",
    "date",
    "which",
    "type",
    "whereis",
    "uname",
    "id",
    "ls",
    "dir",
    "wc",
    "head",
    "tail",
    "grep",
    "egrep",
    "fgrep",
    "find",
    "file",
    "stat",
    "cat",
    "diff",
    "sed",
    "awk",
    "cut",
    "sort",
    "uniq",
    "tr",
    "jq",
    "tree",
    "echo",
    "printf",
    "mkdir",
    "touch",
    "cp",
    "mv",
    "pytest",
    "uv",
    "agy",
    "matgit",
    "ssh",
    "scp",
}

PYTHON_DANGEROUS_MODULES = {
    "os",
    "sys",
    "subprocess",
    "shutil",
    "pty",
    "socket",
    "requests",
    "urllib",
    "ctypes",
    "builtins",
}
PYTHON_DANGEROUS_CALLS = {
    "eval",
    "exec",
    "__import__",
    "compile",
    "open",
}

# UIスピナーや非コマンド文字列の検知用
SPINNER_CHARS_RE = re.compile(r"[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏⣾⣽⣻⢿⡿⣟⣯⣷]")


def is_valid_command_candidate(cmd: str) -> bool:
    """Check if the extracted text looks like a real shell command rather than UI noise."""
    clean = cmd.strip()
    if not clean:
        return False
    # スピナー文字が含まれる場合はUIゴミデータ
    if SPINNER_CHARS_RE.search(clean):
        return False
    # 通常の英文文章（英語の文末ピリオドや、"The checkerboard pattern..." のような自然言語）を排除
    if re.search(r"^[A-Z][a-z]+ [a-z]+ .+\.$", clean):
        return False
    if "is..." in clean or "Editing fi" in clean:
        return False
    return True


def _inspect_python_code_safety(code: str) -> tuple[bool | None, str]:
    """Parse inline Python code via AST to ensure it does not perform dangerous operations."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        # シェルのクォートやエスケープによる構文解析失敗時は、静的判定不能（None）として審査役AIへ委ねる
        return None, "構文解析不能（審査役AIへ委任）"

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_pkg = alias.name.split(".")[0]
                if root_pkg in PYTHON_DANGEROUS_MODULES:
                    return False, f"危険なPythonモジュール（{root_pkg}）のインポートが検出されました。"
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root_pkg = node.module.split(".")[0]
                if root_pkg in PYTHON_DANGEROUS_MODULES:
                    return False, f"危険なPythonモジュール（{root_pkg}）からのインポートが検出されました。"
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id in PYTHON_DANGEROUS_CALLS:
                    return False, f"危険な関数（{node.func.id}）の呼び出しが検出されました。"

    return True, "安全なPythonコードです。"


def _split_subcommands(cmd: str) -> list[list[str]]:
    """Split pipeline or chained commands (&&, ||, ;, |) into individual token lists, preserving quotes."""
    try:
        tokens = shlex.split(cmd)
    except Exception:
        tokens = cmd.split()

    subcmds: list[list[str]] = []
    current: list[str] = []
    for t in tokens:
        if t in ("&&", "||", ";", "|"):
            if current:
                subcmds.append(current)
                current = []
        else:
            current.append(t)
    if current:
        subcmds.append(current)
    return subcmds


def evaluate_fast_filter(cmd: str) -> tuple[str, str] | None:
    """Evaluate command locally in microseconds to save tokens and achieve instant response.

    Returns:
        ("APPROVE", reason) or ("REJECT", reason) or None (needs LLM review)
    """
    clean_cmd = cmd.strip()
    if not clean_cmd:
        return ("APPROVE", "【高速判定】空のコマンドです。（トークン: 0）")

    # UIゴミデータや自然言語文章のチェック
    if not is_valid_command_candidate(clean_cmd):
        return ("REJECT", "【高速ガード拒否】有効なシェルコマンドではなく、UIログ・画面表示のゴミデータが検出されました。")

    # 1. 明白な危険パターンの全コマンド横断チェック（即時 REJECT）
    for pattern, reason in DANGEROUS_PATTERNS:
        if re.search(pattern, clean_cmd):
            return ("REJECT", f"【高速ガード拒否】{reason}")

    # 2. 絶対パスのチェック（/home/ や /root/ などのハードコード）
    # ※ただし SSH/SCP コマンドにおけるリモート側の作業ディレクトリ（例: pi@... "/home/pi/..."）は規約上許可
    is_remote_cmd = clean_cmd.startswith(("ssh ", "scp ", "rsync "))
    if not is_remote_cmd:
        words = clean_cmd.split()
        for w in words:
            clean_w = w.strip("'\";,()[]{}")
            if clean_w.startswith("/home/") or clean_w.startswith("/root/") or clean_w.startswith("/mnt/"):
                return ("REJECT", "【高速ガード拒否】ローカル絶対パスのハードコードは禁止されています（相対パスを使用してください）。")

    # 3. バイナリファイルの直接閲覧チェック
    if any(clean_cmd.startswith(prefix) for prefix in ["cat ", "view ", "less ", "more ", "head ", "tail "]):
        for ext in BINARY_EXTENSIONS:
            if ext in clean_cmd:
                return ("REJECT", f"【高速ガード拒否】バイナリ・画像ファイル（{ext}）の直接読み込みは禁止されています。")

    # 4. Google Drive / クラウドストレージ上の生 git コマンドチェック
    cwd_str = os.getcwd().lower()
    is_cloud_storage = "gdrive" in cwd_str or "rclone" in cwd_str or "box_storage" in cwd_str or "gdrive" in clean_cmd.lower()
    if is_cloud_storage and re.search(r"\bgit\s+(status|diff|add|commit|push|pull|branch|log|checkout)\b", clean_cmd):
        if "matgit" not in clean_cmd and "--git-dir" not in clean_cmd:
            return ("REJECT", "【高速ガード拒否】Google Drive等のクラウド同期ディレクトリ配下での生 git コマンド実行は禁止されています（matgit 等を使用してください）。")

    # 5. 仮想環境外での生 pip install / sudo apt のチェック
    if re.search(r"\b(pip|pip3)\s+install\b", clean_cmd) and "uv" not in clean_cmd:
        return ("REJECT", "【高速ガード拒否】グローバル環境汚染を防ぐため、生 pip install は禁止されています（uv や仮想環境を使用してください）。")
    if re.search(r"\bapt(-get)?\s+install\b", clean_cmd):
        return ("REJECT", "【高速ガード拒否】システムパッケージの変更（apt install）は禁止されています。")

    # 6. サブコマンド分割による包括的セーフチェック
    subcmds = _split_subcommands(clean_cmd)
    if not subcmds:
        return ("APPROVE", "【高速判定】安全なコマンドです。（トークン: 0）")

    for tokens in subcmds:

        if not tokens:
            continue

        base = tokens[0].strip("'\";,()")
        base_name = base.split("/")[-1]

        if base_name in ("python", "python3"):
            if "-c" in tokens:
                try:
                    c_idx = tokens.index("-c")
                    if c_idx + 1 < len(tokens):
                        code = tokens[c_idx + 1]
                        code_res, code_reason = _inspect_python_code_safety(code)
                        if code_res is False:
                            return ("REJECT", f"【高速ガード拒否】python -c 内のコード安全性違反: {code_reason}")
                        elif code_res is None:
                            return None
                    else:
                        return ("REJECT", "【高速ガード拒否】python -c にコードが指定されていません。")
                except Exception:
                    return None
            elif "-m" in tokens:
                try:
                    m_idx = tokens.index("-m")
                    if m_idx + 1 < len(tokens):
                        mod = tokens[m_idx + 1]
                        if mod in ("py_compile", "compileall", "unittest", "pytest", "json.tool"):
                            continue
                except Exception:
                    pass
                return None
            else:
                # スクリプトファイル実行（python script.py 等）
                # スクリプトファイルの内容はファイルハッシュおよび差分審査が必要なため、
                # 高速フィルタで素通りさせず、キャッシュ／審査役に委ねる。
                return None

        elif base_name not in SAFE_BASE_COMMANDS:
            return None

    return ("APPROVE", "【高速判定】すべてのコマンド要素が安全基準を満たしています。（トークン: 0, 判定時間: 即時）")
