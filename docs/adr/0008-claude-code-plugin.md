# 0008. Claude Code のプラグインとして配り、Claude Code を開いたときにダッシュボードを起動する

- ステータス: 承認(いつも有効なアカウントで GitHub を操作する部分は 0018、SessionStart のフックで起動する部分は 0020 で置き換え)
- 日付: 2026-11-16

## 文脈

- これまでは `python3 server.py` を手で起動していた。起動し忘れると、報告は inbox に置かれ、画面も開けない
- 高橋さんから、ほかの人にも配れるよう製品にしたい、毎回の起動をなくしたい、と頼まれ、プラグインにすると決めた
- Claude Code のプラグインには、スキル、フック、`bin/`(有効なあいだ Bash の PATH に入る)を入れられる。同じリポジトリを marketplace にもでき、`/plugin marketplace add` と `/plugin install` で入る
- プラグインのファイルはバージョンごとに `~/.claude/plugins/cache/` に写され、更新で場所が変わる。`CLAUDE_PLUGIN_DATA` は更新で消えないが、フックにしか渡らず、Claude が実行するコマンドからは読めない

## 検討した選択肢

起動のしかた

1. SessionStart のフックで、止まっていれば裏で起動する。報告のコマンドも、止まっていれば起動してから送る
2. プラグインの monitors で、セッションのあいだだけ動かす
3. OS の自動起動(launchd、systemd)を入れるコマンドを配る
4. ネットに置く(SaaS)

データの置き場所

- A. `PR_DASHBOARD_HOME`、なければ `${XDG_DATA_HOME:-~/.local/share}/pr-dashboard`
- B. `CLAUDE_PLUGIN_DATA`
- C. これまでどおりコードの隣の `data/` と `inbox/`

## 決定

起動は 1、データは A にする。

- リポジトリのルートをプラグイン(名前は `pr-dashboard`)にし、`.claude-plugin/marketplace.json`(名前は `h11roki`)で配る。入れ方は `/plugin marketplace add h11roki/pr-dashboard` と `/plugin install pr-dashboard@h11roki`
- `bin/pr-dashboard-start`: 8787 番に何もいなければ、サーバーを `nohup` で裏に起動する。何度呼んでも 1 つだけ動く。`--restart` で止めてから起動し直す
- サーバーはデータの置き場所を作業ディレクトリにし、`GH_TOKEN` と `GITHUB_TOKEN` を外して起動する。起動したセッションの作業ディレクトリ(あとで消える worktree)や、そのディレクトリだけのトークンを引き継がず、いつも `gh auth login` のアカウントで GitHub を操作する
- `hooks/hooks.json`: SessionStart で `pr-dashboard-start` を呼ぶ。標準出力には何も出さない(Claude の文脈に入るため)
- `bin/pr-dashboard-report`: これまでの `scripts/report-pr.sh`。送り先を指定していないときは、つながらなければ `pr-dashboard-start` で起動してから送り直し、それでもだめなら inbox に置く
- スキル `report`(`/pr-dashboard:report`): 判断材料の JSON の書き方と送り方。どの PR の手順からも呼べるようにし、pr-review-fix の手順 7 はこのスキルに従う形にする
- スキル `open`(`/pr-dashboard:open`): 起動して画面を開く。高橋さんが呼ぶときだけ動かす
- データ(`prs.db`、`inbox/`、`server.log`、`server.pid`)は A に置く。サーバーと 2 つのコマンドが同じ決まりで場所を決める
- Python は 3.13 以上の `python3` を使い、なければ uv で 3.13 を用意して動かす

## 理由

- 1 なら、Claude Code を使っている人は何もしなくても起動している。フックだけでは起動しなかったときも、報告のコマンドが起動するので報告は届く
- 2 はセッションを閉じるとサーバーも止まり、画面を開いておけない。実験的な機能で、出力が Claude への通知になる
- 3 は OS ごとに作り方が違い、入れる手順も増える。プラグインを入れるだけで済む 1 のほうが配りやすい
- 4 はサーバー代がかかり、GitHub の権限を預かることになる(0001)
- データの B はフックにしか渡らず、Claude が実行する報告のコマンドから inbox の場所が分からない。C はプラグインの更新でコードの場所が変わるたびにデータを失う

## 受け入れたトレードオフ

- フックで起動したサーバーが Claude Code を閉じたあとも動き続けることは、ドキュメントに書かれていない。手元で確かめ、止まっていても次のセッションか報告のときに起動し直す
- プラグインを更新しても、動いているサーバーは古いコードのまま。`pr-dashboard-start --restart` で起動し直す
- データはプラグインを消しても残る。消すときは手で消す
- これまで `data/` にあったデータは、新しい場所に一度だけ手で移す
- 環境変数のトークンだけで `gh` を使っている人は、`gh auth login` をしないとボタンが使えない

## 再検討する条件

- Claude Code 以外の人にも配りたくなったとき(3 の自動起動を足す)
- プラグインの monitors が正式になり、セッションをまたいで動かせるようになったとき
