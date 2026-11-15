---
name: open
description: PR トリアージのダッシュボードを起動して、ブラウザで開く
disable-model-invocation: true
allowed-tools: Bash(pr-dashboard-start *), Bash(open http://127.0.0.1:8787), Bash(xdg-open http://127.0.0.1:8787)
---

# ダッシュボードを開く

1. `pr-dashboard-start` を実行する。動いていなければ裏で起動し、動いていれば何もしない
2. macOS なら `open http://127.0.0.1:8787`、Linux なら `xdg-open http://127.0.0.1:8787` で開く
3. 開けなかったときは、http://127.0.0.1:8787 をブラウザで開くよう伝える。起動の記録は `~/.local/share/pr-dashboard/server.log` にある

プラグインを更新したあとは、`pr-dashboard-start --restart` で起動し直すと新しい版になる。
