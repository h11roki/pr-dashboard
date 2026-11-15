---
name: open
description: PR トリアージのダッシュボードを起動して、ブラウザで開く
disable-model-invocation: true
allowed-tools: Bash(pr-dashboard-start), Bash(pr-dashboard-start *), Bash(open http://127.0.0.1:8787), Bash(xdg-open http://127.0.0.1:8787)
---

# ダッシュボードを開く

1. `pr-dashboard-start` を実行する。動いていなければ裏で起動してつながるまで待ち、動いていれば何もしない
2. 1 が失敗したら、出た理由と記録の場所をそのまま伝えて終わる
3. macOS なら `open http://127.0.0.1:8787`、Linux なら `xdg-open http://127.0.0.1:8787` で開く。開けなかったときは、http://127.0.0.1:8787 をブラウザで開くよう伝える

プラグインを更新したあとは、`pr-dashboard-start --restart` で起動し直すと新しい版になる。
