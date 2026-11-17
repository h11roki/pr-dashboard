# ADR(設計判断の記録)

設計上の判断を、判断した時点の文脈と一緒に残す。後から「なぜこうなっているか」を読めるようにし、前提が変わったときに見直せるようにする。

## 書く契機

- 選択肢が2つ以上あり、後から戻しにくい判断をしたとき
- 実装より先に書く

## ルール

- 置き場は `docs/adr/NNNN-短いタイトル.md`。番号は4桁の連番で欠番を作らない
- 一度承認した ADR は書き換えない。覆すときは新しい ADR を書き、古い方のステータスを「廃止(Superseded by NNNN)」にする
- 1ファイル1テーマ。区切りは見出し階層で表し、水平線は使わない。図は Mermaid
- 項目は下のテンプレートに従う

## テンプレート

```markdown
# NNNN. 判断を一文で

- ステータス: 提案 / 承認 / 廃止(Superseded by NNNN)
- 日付: YYYY-MM-DD

## 文脈

## 検討した選択肢

## 決定

## 理由

## 受け入れたトレードオフ

## 再検討する条件
```

## 一覧

| 番号 | タイトル | ステータス |
|---|---|---|
| [0001](0001-local-dashboard.md) | 手元の PC だけで動く 1 枚の画面にし、Python の標準ライブラリと SQLite で作る | 承認 |
| [0002](0002-report-after-artifact.md) | 報告は pr-review-fix の最後に 1 回だけ送り、PR は Draft で作る | 承認 |
| [0003](0003-refuse-foreign-requests.md) | サーバーは Host、Origin、Content-Type を確かめ、ほかのサイトからの要求を断る | 提案 |
| [0004](0004-shadcn-look-in-plain-css.md) | 画面の見た目は shadcn/ui に合わせ、React を使わずに CSS で写す | 承認 |
| [0005](0005-group-by-project.md) | PR はリポジトリごとにまとめて見せ、スイッチでまとめない表示に戻せるようにする | 承認 |
| [0006](0006-report-script-failures.md) | 送信スクリプトは送る前にサーバーと同じ検証をし、届かなかったと分かるときだけ inbox に置く | 承認(Python の版は 0019 で置き換え) |
| [0007](0007-decision-buttons.md) | 判断のボタンはサーバーから gh を -R 付きで呼び、失敗したら判断の状態を変えない | 承認(差し戻しは 0011 で置き換え) |
| [0008](0008-claude-code-plugin.md) | Claude Code のプラグインとして配り、Claude Code を開いたときにダッシュボードを起動する | 承認(GitHub のアカウントは 0018、Python の版は 0019 で置き換え) |
| [0009](0009-name-toriage.md) | 名前を toriage にする | 承認 |
| [0010](0010-server-stops-itself.md) | 起動し直すときは、サーバーに自分で止まってもらう | 承認 |
| [0011](0011-return-to-the-session.md) | 差し戻しは、PR を出した Claude のセッションを裏で再開して理由を渡す | 承認 |
| [0012](0012-resume-with-the-current-report-command.md) | 裏で再開する Claude には、サーバーが持つ toriage の bin を PATH から外して渡す | 承認 |
| [0013](0013-open-the-dashboard-after-reporting.md) | 送信スクリプトは、報告がダッシュボードに届いたらブラウザで開く | 廃止(Superseded by 0017) |
| [0014](0014-support-codex.md) | Codex でも使えるようにし、差し戻しは PR を作ったエージェントの会話を裏で再開する | 承認 |
| [0015](0015-thread-view.md) | 画面を、左に PR の一覧、右に選んだ PR の中身を出す 2 ペインにする | 承認 |
| [0016](0016-move-closed-prs.md) | マージや close をした PR は、画面を開いたときに GitHub で確かめて「済み」に移す | 承認 |
| [0017](0017-notify-instead-of-opening.md) | 報告が届いたら、ページを開かずに OS の通知で知らせる | 承認 |
| [0018](0018-act-as-the-reporting-account.md) | GitHub の操作は、報告を送ったセッションの gh のアカウントで行う | 承認 |
| [0019](0019-run-on-python-39.md) | Python 3.9 で動かし、送信と起動のスクリプトも Python にする | 承認 |
