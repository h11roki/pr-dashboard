# toriage

Claude Code が作った PR の判断材料を受け取り、手元のブラウザで「レビューに出すか」を決めて、その場で反映する(レビュー依頼は GitHub に、差し戻しは PR を作った Claude に)ダッシュボード。Claude Code のプラグインとして配る。設計判断は `docs/adr/`、元になった設計書は「PRトリアージダッシュボード 企画・設計書」(旧名のまま)、使う人向けの説明は `README.md`。

## コマンド

- 起動: `bin/toriage-start`(裏で起動してつながるまで待つ。何度呼んでも 1 つ。`--restart` で起動し直す)。前で動かすなら `python3 server.py`。どちらも `http://127.0.0.1:8787`
- プラグインとして試す: `claude --plugin-dir .`、検証は `claude plugin validate .`
- `uv sync`
- `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`(CI と同じ)

## 構成

- `server.py`: 報告の受け取り、検証、SQLite への保存、一覧と判断の API、画面の配信。実行時は Python の標準ライブラリだけを使う
- `static/index.html`: 画面。HTML と素の JavaScript の 1 ファイル。サーバーは起動時に 1 回だけ読むので、書き換えたら起動し直す
- `bin/toriage-report`: スキル `report` で Claude Code が呼ぶ送信スクリプト(プラグインが有効なあいだ Bash の PATH に入る)。Claude の判断材料(標準入力の JSON)に `gh pr view` の数字と、`CLAUDE_CODE_SESSION_ID` と実行した場所から作る `session` を足し、`server.py --check`(サーバーと同じ `parse_body` の検証)を通してから送る。つながらなければ `toriage-start` で起動して送り直し、届かなかったと分かるとき(curl の 6、7)だけ inbox に保存する(0006、0008)。終わり方は 0 が送ったか inbox に保存した、1 が理由を出して失敗、2 が使い方の誤り。`gh`、`jq`、`curl` が要る。送り先は `TORIAGE_URL` で変えられ、そのときは起動しない
- `bin/toriage-start`: サーバーを `nohup` で裏に起動し、最大 10 秒つながるまで待つ。作業ディレクトリはデータの置き場所にし、`GH_TOKEN` と `GITHUB_TOKEN` は渡さない(0008)。起動できなければ `server.log` の場所を出して 1 で終わる。`--restart` は 8787 番で答えているサーバーに `POST /api/shutdown` で止まってもらい(置き場所や版によらない)、止まらなければ 1 で終わる(0010)。`server.pid` は番号を知るためだけに残す。SessionStart のフック(`hooks/hooks.json`、待ち時間はフックの 15 秒より短くする)、スキル `open`、送信スクリプトが呼ぶ
- `libexec/toriage-python`: 3.13 以上の `python3`、なければ uv の Python 3.13 で実行する
- `libexec/toriage-home`: データの置き場所を出す。`server.py` の `data_home()` と同じ決まりで、テストで一致を確かめている
- `skills/report`、`skills/open`: `/toriage:report`(判断材料の書き方と送り方)、`/toriage:open`(起動して開く。人が呼ぶときだけ)
- `.claude-plugin/`: `plugin.json` と `marketplace.json`(同じリポジトリを marketplace `h11roki` にする)
- データ: `TORIAGE_HOME`、なければ `${XDG_DATA_HOME:-~/.local/share}/toriage`(どちらも相対パスなら使わない)。`prs.db`(消せばリセット)、`inbox/`(起動時に取り込む。取り込めたものと保存済みより古いものは消し、読めないものは残す)、`server.log`、`server.pid`(待ち受けを始めたサーバーが自分で書く)、`returns/<セッション ID>.log`(差し戻しで裏で再開した Claude の出力)
- `docs/images/`: README のスクショ(架空の `acme` のリポジトリで撮る)と構成図。構成図は [stack-diagram](https://github.com/caru-ini/stack-diagram) で描いた `architecture.html` を、明るい見た目と暗い見た目の PNG に撮ったもの(図は Mermaid という決まりの例外。高橋さんの依頼)。描き直すときは HTML を直して撮り直す

## 決まっていること(詳細は ADR)

- 手元の PC だけで動く 1 枚の画面にし、標準ライブラリと SQLite で作る。数字は `gh` から、判断材料は Claude が書く(0001)
- 報告は pr-review-fix の最後に 1 回だけ送り、`artifact_url` を付ける。PR は Draft で作る(0002)
- Host、Origin、Content-Type を確かめ、ほかのサイトからの要求を断る(0003、提案)
- 画面の見た目は shadcn/ui の色の変数と部品の形を素の CSS に写す。React とビルドは入れない(0004)
- PR はリポジトリごとにまとめて見せる。見出しは名前の順、スイッチでまとめない表示に戻せる。折りたたみは保存しない(0005)
- 送信スクリプトは送る前にサーバーと同じ検証をし、届かなかったと分かるときだけ inbox に置く(0006)
- 判断のボタン(レビュー依頼、差し戻し、保留)は未判断と保留のカードに出す。レビュー依頼はサーバーから `gh` を `-R` 付きで呼び、失敗したら状態を変えない(0007)
- 差し戻しは PR にコメントせず、報告の `session` の `workdir` で `claude -p --resume <id> --permission-mode auto --permission-prompts none` を裏で起動して理由を渡す。起動できなければ状態を変えない。`session` のない報告には差し戻しを出さない。差し戻しのカードには、やり直し用に差し戻しだけを出す。同じセッションの再開が動いているあいだ(`returns/<id>.pid` のプロセスが生きているあいだ)は断る(0011)
- 裏で再開する `claude` に渡す PATH から、toriage のプラグインの `bin`(`toriage-report` があり、親に `.claude-plugin/plugin.json` がある)を外す。サーバーが起動したときの古い版の `bin` が先に見つからないようにし、Claude Code が足す今の版を使わせる(0012)
- Claude Code のプラグインとして配り、SessionStart のフックと送信スクリプトがサーバーを起動する。データはプラグインの外に置く(0008)
- 名前は toriage(小文字)。リポジトリ、ディレクトリ、プラグイン、コマンド、データの場所、環境変数をそろえる。0001〜0008 の古い名前はこれで読み替える(0009)
- 起動し直すときは、8787 番で答えているサーバーに `POST /api/shutdown` で自分で止まってもらう(0010)

## 進め方

- フェーズ: 1 受け取って表示する、2 Claude Code とつなぐ、3 判断をその場で実行する、4 使いながら足す
- 新しい設計判断は先に ADR を書き、`docs/adr/README.md` の一覧を更新する
- Python 3.13、mypy strict。コメントは書かず、経緯はコミットメッセージに書く
- 公開リポジトリなので、README の見本、スクショ、テストに、実在の利用者のリポジトリ名、セッション ID、個人の情報を入れない(架空の `acme` を使う)。このリポジトリ自身の名前と、使った道具への出典のリンクは書いてよい
