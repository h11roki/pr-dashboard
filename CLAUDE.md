# pr-dashboard

Claude Code が作った PR の判断材料を受け取り、手元のブラウザで「レビューに出すか」を決めて、その場で GitHub に反映するダッシュボード。設計判断は `docs/adr/`、元になった設計書は「PRトリアージダッシュボード 企画・設計書」。

## コマンド

- 起動: `python3 server.py`(`http://127.0.0.1:8787` を開く)
- `uv sync`
- `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`(CI と同じ)

## 構成

- `server.py`: 報告の受け取り、検証、SQLite への保存、一覧の API、画面の配信。実行時は Python の標準ライブラリだけを使う
- `static/index.html`: 画面。HTML と素の JavaScript の 1 ファイル。サーバーは起動時に 1 回だけ読むので、書き換えたら起動し直す
- `data/prs.db`: 起動時に作られる。消せばリセットできる
- `inbox/`: 送れなかった報告の退避先。起動時に取り込む。取り込めたものと、保存済みより古いものは消し、読めないものは残す

## 決まっていること(詳細は ADR)

- 手元の PC だけで動く 1 枚の画面にし、標準ライブラリと SQLite で作る。数字は `gh` から、判断材料は Claude が書く(0001)
- 報告は pr-review-fix の最後に 1 回だけ送り、`artifact_url` を付ける。PR は Draft で作る(0002)
- Host、Origin、Content-Type を確かめ、ほかのサイトからの要求を断る(0003、提案)
- 画面の見た目は shadcn/ui の色の変数と部品の形を素の CSS に写す。React とビルドは入れない(0004)
- PR はリポジトリごとにまとめて見せる。見出しは名前の順、スイッチでまとめない表示に戻せる。折りたたみは保存しない(0005)

## 進め方

- フェーズ: 1 受け取って表示する、2 Claude Code とつなぐ、3 判断をその場で実行する、4 使いながら足す
- 新しい設計判断は先に ADR を書き、`docs/adr/README.md` の一覧を更新する
- Python 3.13、mypy strict。コメントは書かず、経緯はコミットメッセージに書く
