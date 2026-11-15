# 0009. 名前を toriage にする

- ステータス: 承認
- 日付: 2026-11-16

## 文脈

- 製品名の「PRトリアージ」とリポジトリ名の `pr-dashboard` は、何をするかを説明しているだけで、名前として覚えにくい。高橋さんから、ダサいので案がほしいと頼まれた
- 高橋さんのほかのプロジェクトは shirube(道標)、kadoban(門番)、sekimori(関守)と、道や関所にまつわる日本語の名前にしている
- 0008 で公開し、プラグインとして配り始めたところで、入れているのは高橋さんだけ。名前を変えるなら今が一番安い

## 検討した選択肢

| 案 | 意味 | GitHub の同名 |
|---|---|---|
| mekiki(目利き) | 品の良し悪しを見分ける目 | 33 件、最大 3★。AI の評価や推薦の道具にも使われる |
| tefuda(手札) | 手元の札。PR がカードで並ぶ画面と合う | 9 件、すべて 0★ |
| toriage(取り上げ) | triage のもじりで、「取り上げる(PR を審議にかける)」とも読める | 2 件 |
| sabaki(捌き・裁き) | 仕事を捌く、裁く | 囲碁のアプリ Sabaki(2,800★)と同じで、検索で埋もれる |

## 決定

名前を toriage にし、表記は小文字にする。ほかのプロジェクトと同じ書き方。

| 対象 | 前 | 後 |
|---|---|---|
| リポジトリ | `h11roki/pr-dashboard` | `h11roki/toriage` |
| 手元のディレクトリ | `~/private/myprojects/pr-dashboard` | `~/private/myprojects/toriage` |
| プラグイン | `pr-dashboard@h11roki` | `toriage@h11roki`(marketplace の名前 `h11roki` は変えない) |
| スキル | `/pr-dashboard:report`、`/pr-dashboard:open` | `/toriage:report`、`/toriage:open` |
| コマンド | `pr-dashboard-report`、`pr-dashboard-start` | `toriage-report`、`toriage-start` |
| `libexec/` | `pr-dashboard-python`、`pr-dashboard-home` | `toriage-python`、`toriage-home` |
| データ | `${XDG_DATA_HOME:-~/.local/share}/pr-dashboard` | `${XDG_DATA_HOME:-~/.local/share}/toriage` |
| 環境変数 | `PR_DASHBOARD_HOME`、`PR_DASHBOARD_URL` | `TORIAGE_HOME`、`TORIAGE_URL` |
| 画面の題 | PRトリアージ | toriage |

- 0001〜0008 の本文は書き換えず、そこに出てくる古い名前は上の表で読み替える
- 古い名前の環境変数とデータの場所は読まない。入れていた人は、データを一度だけ手で移す

## 理由

- triage の言い換えなので、PR を選り分ける道具だと名前から分かる。「取り上げる」と読めば、どの PR を審議にかけるかを決める道具という意味にもなる
- 同名の公開リポジトリが 2 件しかなく、検索で埋もれない
- 古い名前を読み続ける作りにすると、名前の決まりが 2 通りになる。入れているのは高橋さんだけなので、手で移す 1 回で済ませる

## 受け入れたトレードオフ

- 言葉遊びなので、英語を使う人には triage の打ち間違いに見えうる。README の冒頭で読みと意味を書く
- 古い URL は GitHub の転送に頼る。転送は、同じ名前のリポジトリを新しく作ると切れる
- 画面の「プロジェクトごとにまとめる」の設定(ブラウザに保存)のキーも変わるので、一度だけ既定(まとめる)に戻る

## 再検討する条件

- 英語を使う人が増え、打ち間違いと思われることが問題になったとき
