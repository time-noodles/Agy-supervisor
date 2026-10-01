# Agy-supervisor

**Antigravity CLI 自律AI審査役 ＆ 超高速セキュリティガード（マルチプラットフォーム・ゼロ依存）**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python: 3.9+](https://img.shields.io/badge/Python-3.9%2B-blue.svg)](https://www.python.org/)
[![Platform: Win / Linux / macOS](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-green.svg)]()

Antigravity CLI（`agy`）の実行確認プロンプト（`RunCommand` などの Y/n プロンプト）を自動監視し、**審査役AI（`agy -p` ワンショット実行）** および **包括的超高速ローカルガード** によって安全性を自動判定して承認（`y`）または拒否（`n`）を行う Supervisor ラッパーです。

Windows 10/11（ConPTY / ctypes）、Linux（POSIX PTY）、macOS に完全対応し、**サードパーティ外部ライブラリへの依存ゼロ（Python 標準ライブラリのみ）** で動作します。

---

## 🌟 特徴・コア機能

1. **マルチプラットフォーム完全対応（Windows / Linux / macOS）**
   - **ゼロ依存（Zero External Dependencies）**: `winpty` やサードパーティ C 拡張を一切使用せず、Windows 10/11 標準の **ConPTY（Windows PseudoConsole API via `ctypes`）** および POSIX 標準の `pty` / `termios` でネイティブ動作。
   - **OS ネイティブ通知**: Windows（PowerShell Toast 通知）、Linux（`notify-send`）、macOS（`osascript`）を自動判別し、非同期でデスクトップ通知を配信。
   - **ワンコマンド・インストーラー完備**: Windows（PowerShell）、Linux/macOS、Python pip パッケージ（`pyproject.toml`）に対応。

2. **コマンド注目箇所の自動抽出（Command Focus）による審査の爆速化**
   - 長大な SSH オプション（`-o StrictHostKeyChecking=no ...`）やシェルラッパー（`bash -c`）を自動アンラップ。
   - **「何が実行されるのか（コア操作）」、「対象ファイル・パス」、「事前静的スキャン結果」にピンポイントで注目箇所を絞り込んでAIに提示**。
   - プロンプトサイズを 85% 削減（約2,400文字 → 300文字）し、審査時間を 15秒超 → 2〜4秒 に劇的短縮。タイムアウトによる誤爆リジェクトを根絶。

3. **拒否時の自律リカバリーフィードバック機構（他の方法を自動探索）**
   - コマンドが安全規約により拒否された場合、ダイアログキャンセル直後に **「拒否理由」と「安全な代替コマンドで作業を続行せよ」というガイダンスをエージェントに自動注入**。
   - エージェントが作業を中断したり諦めて終了したりするのを防ぎ、自律的に別のアプローチや相対パスでの実行を探索・続行。

4. **結果が画面から消えない固定 9 行リッチダッシュボードパネル（常駐フッター）**
   - ターミナルの物理最下部 9 行を専有（`agy` の描画領域を `rows - 9` に制限し、DECSTBM スクロールマージンで固定）。
   - **TUI画面（BubbleTea）がどれだけ再描画・スクロールされても 1ミリ秒たりとも消えない専用ダッシュボード** を常時描画。
   - **行 1 (ヘッダー＆統計)**: `[✔ APPROVE]` / `[✖ REJECT]` / `[⏳ 審査中]` バッジ ＋ セッション統計（承認数、拒否数、⚡即時判定数、推計節約トークン数）。
   - **行 2〜5 (コマンド 4行割り当て ＆ ディレクトリ)**: `💻 CMD:` から最大 4 行にわたり、長いコマンドやスクリプト引数を単語境界で美しく展開。最終行に `📁 CWD` を右寄せ配置。
   - **行 6〜9 (理由 4行割り当て ＆ 判定元)**: `📝 理由:` から最大 4 行にわたり、AI審査や安全判定の根拠を詳細に展開。最終行に判定元バッジ（`[高速ガード]` / `[学習キャッシュ]` / `[審査役AI]`）と所要時間を右寄せ配置。

5. **プロジェクト別学習キャッシュ（スクリプトSHA-256整合性検証 ＆ 爆速0秒承認）**
   - スクリプト実行（`python` / `bash` 等）の実行対象ファイルの **SHA-256 ハッシュを自動追跡**。
   - スクリプトが1文字でも書き換わった場合はキャッシュが自動無効化（Cache Invalidation）され、直前の変更差分（diff）を抽出して審査役AIが厳密に再審査。
   - 同一コードの再実行時は、コードの同一性が数学的に保証された状態で **0.001秒・トークン0で即時承認**。

6. **二重の永続監査ログ（全体ログ ＆ プロジェクト別ログ）**
   - **全体共通ログ**: `~/.gemini/supervisor/audit.log`（全セッション・全プロジェクトの時系列監査）
   - **プロジェクト別ログ**: `~/.gemini/supervisor/projects/<プロジェクト名_ハッシュ>/audit.log`（プロジェクトごとの独立した承認・拒否履歴）
   - 全ての実行コマンド、作業ディレクトリ、判定、理由をタイムスタンプ付きで常時記録。

7. **公式 transcript.jsonl 直結のミリ秒コマンド抽出＆差分コンテキスト連携**
   - 画面の文字列パース（TUIスクレイピング）を廃止し、agy 公式の会話ログからミリ秒単位で正確なコマンド（`CommandLine`）、作業ディレクトリ（`Cwd`）、直前の編集差分（`diff`）を直接取得。
   - ターミナルのスピナーや描画更新ラグによる誤検知・コマンド抽出失敗を 100% 根絶。

---

## 📋 審査基準・コアセキュリティルール

| # | ルール区分 | 審査内容（拒否基準） |
| :---: | :--- | :--- |
| **1** | **パス指定（可搬性）** | ローカル絶対パス（`/home/...`, `C:\...` 等）のハードコード禁止（相対パス・環境変数必須）。 |
| **2** | **バイナリ直接読込禁止** | `.tif`, `.tiff`, `.png`, `.jpg`, `.npy`, `.h5` などを `cat` や `view` で直接開く行為の禁止。 |
| **3** | **リモート操作安全保護** | SSH接続先等で、システム領域の改変や再起動・電源断を行うコマンドの禁止。 |
| **4** | **Google Drive Git分離** | Google Drive（rclone）配下での生 `git` コマンド禁止（`matgit` または `--git-dir`/`--work-tree` 必須）。 |
| **5** | **ログ出力絞り込み** | 巨大出力による画面・コンテキスト圧迫を防止（`head`, `tail`, `grep` 等での絞り込み必須）。 |
| **6** | **破壊的削除・巻き戻し禁止** | `rm -rf *`, `rmdir /s`, `git reset --hard` 等の不可逆なデータ損失コマンドの禁止。 |
| **7** | **クレデンシャル漏洩防止** | `env`, `printenv`, `cat ~/.ssh/*`, `.env` ファイル等のシークレットをコンソール出力する行為の禁止。 |
| **8** | **グローバル環境汚染防止** | 仮想環境外での生 `pip install` や勝手な管理者権限パッケージ導入の禁止。 |
| **9** | **永続プロセス・無限ループ禁止** | `while true`, `for ((;;))`, `tail -f`, Webサーバー直接起動等によるフリーズの禁止。 |
| **10** | **特権昇格禁止** | `sudo` コマンドおよびシステムディレクトリのパーミッション変更の禁止。 |

---

## 🚀 導入手順（ワンクリック・インストール）

### 🪟 Windows の場合 (PowerShell)

管理者権限は不要です。PowerShell を開いてリポジトリをクローンして実行します：

```powershell
git clone https://github.com/time-noodles/Agy-supervisor.git
cd Agy-supervisor
```

#### 方法 A: PowerShell プロファイルへ自動登録（推奨）
```powershell
.\install.ps1
```
完了後、PowerShell を再起動（または `. $PROFILE`）すれば、`agy-c` で Supervisor を起動できます。

#### 方法 B: pip でパッケージ登録
```powershell
pip install -e .
```
完了後、どのディレクトリからでも `agy-sup -c` で起動できます。

---

### 🐧 Linux / 🍎 macOS の場合

ターミナルを開いてリポジトリをクローンして実行します：

```bash
git clone https://github.com/time-noodles/Agy-supervisor.git
cd Agy-supervisor
```

#### 方法 A: シェルプロファイルへ自動登録（推奨）
```bash
./install.sh
```
完了後、`source ~/.bashrc`（または `source ~/.zshrc`）を実行すれば、`agy-c` で Supervisor を起動できます。

#### 方法 B: pip でパッケージ登録
```bash
pip install -e .
```
完了後、どのディレクトリからでも `agy-sup -c` で起動できます。

---

## 💻 起動コマンド・オプション

### 1. 前回の会話を再開して起動（通常作業）
```bash
# Linux / macOS
agy-c

# Windows PowerShell
agy-c

# pip インストール環境
agy-sup -c
```

### 2. 新規セッションとして起動
```bash
# Linux / macOS
~/.gemini/supervisor/run-supervisor.sh

# Windows PowerShell
.\run-supervisor.ps1

# Windows コマンドプロンプト (CMD)
run-supervisor.bat
```

### 3. リアルタイム監査ログの確認（別ウィンドウ／ペイン）
```bash
# 全体共通ログ（すべてのプロジェクト）
tail -f ~/.gemini/supervisor/audit.log

# プロジェクト個別ログ（カレントプロジェクトの履歴のみ）
# ~/.gemini/supervisor/projects/<プロジェクト名_ハッシュ>/audit.log
tail -f ~/.gemini/supervisor/projects/*/audit.log
```

### 4. 審査役AIモデルの切り替え（オプション）
```bash
# フラグで指定する場合（デフォルト: gemini-3.8-flash-low）
agy-c --supervisor-model gemini-3.7-flash-low
```

---

## 📁 ディレクトリ構造

```text
supervisor/
├── supervisor.py          # Supervisor メインエンジン（PTY・ダッシュボード・自律フィードバック）
├── pty_adapter.py         # POSIX PTY & Windows ConPTY 抽象化ドライバ（ゼロ依存）
├── notifier.py            # OS ネイティブ非同期デスクトップ通知（Linux/macOS/Windows）
├── command_focus.py       # コマンド注目箇所抽出（プロンプト85%削減・審査爆速化）
├── fast_filter.py         # 超高速ゼロトークン判定ガード（0.001秒）
├── decision_cache.py      # プロジェクト別学習キャッシュ＆二重監査ログ
├── reviewer.py            # 審査役AI（agy -p）呼び出し・堅牢パーサー
├── rules.py               # 6大核心セキュリティルール（スリム化プロンプト）
├── context_extractor.py   # transcript.jsonl 直結＆差分コンテキスト抽出
├── run-supervisor.sh      # Linux/macOS 用起動スクリプト
├── run-supervisor.ps1     # Windows PowerShell 用起動スクリプト
├── run-supervisor.bat     # Windows CMD 用起動スクリプト
├── install.sh             # Linux/macOS 用自動インストーラー
├── install.ps1            # Windows 用自動インストーラー
├── pyproject.toml         # Python パッケージ設定（agy-sup コマンド化）
├── LICENSE                # MIT ライセンス
└── README.md              # 本ドキュメント
```

---

## ⚠️ 免責事項（Disclaimer）

本ツールは、AIによるコマンド審査および静的ルール評価によってコマンド実行の安全性を向上させる支援ツールです。潜在的に危険な操作の検知率向上に努めていますが、**あらゆる破壊的コマンドや意図しない動作を100%完全に防止することを保証するものではありません**。本ツールの使用によって生じたいかなる損害（データ損失、システム停止、業務上の障害等）についても、作者および貢献者は一切の責任を負いません。重要な操作環境においては、適切なバックアップおよびアクセス権限管理を併用してください。

---

## 📄 ライセンス

本プロジェクトは [MIT License](LICENSE) の下で公開されています。

