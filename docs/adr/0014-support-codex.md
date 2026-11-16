# 0014. Codex でも使えるようにし、差し戻しは PR を作ったエージェントの会話を裏で再開する

- ステータス: 承認
- 日付: 2026-11-16

## 文脈

- toriage は Claude Code のプラグインとして作ってきた(0008)。高橋さんの依頼で、Codex で作った PR も同じダッシュボードで判断し、差し戻せるようにする
- この PC の Codex CLI(0.167、ChatGPT アプリに入っているもの)で調べたこと
  - Codex は `.claude-plugin/marketplace.json` と `.claude-plugin/plugin.json` も読む。toriage をそのままマーケットプレイスとして足し、プラグインとして入れられた。スキルは `toriage:report` と `toriage:open` として読み込まれた
  - Codex は `hooks/hooks.json` を自動で読み、フックに `CLAUDE_PLUGIN_ROOT` を渡す。ただし利用者がフックを確かめて信頼するまで動かない
  - Codex はプラグインの `bin` を PATH に足さない。1 回動かして確かめた
  - Codex はシェルのコマンドに、会話の ID(UUID)を `CODEX_THREAD_ID` で渡す。同じ回で確かめた
  - `codex exec resume <ID> <指示>` で会話を裏で再開できる。`codex queue --thread <ID> --resume` で会話に指示を積むこともできる
  - `codex exec --approve-for-me` は、作業ツリーへの書き込みだけを許すサンドボックスで動かし、それを超える操作を Codex の自動の審査に任せる。設定では `sandbox_mode="workspace-write"`、`approval_policy="on-request"`、`approvals_reviewer="auto_review"` に当たる。`exec resume` にはこの引数が無いので、`-c` で設定する
  - この PC では `codex` が PATH に無く、`/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex` にある

## 検討した選択肢

差し戻しの返し方

1. サーバーが `codex exec resume` で会話を裏で再開する
2. `codex queue --resume` で会話に指示を積む

裏の Codex の権限

1. 自動の審査に任せる
2. サンドボックスも確認も外す(`--dangerously-bypass-approvals-and-sandbox`)

Codex に送信スクリプトを呼ばせる方法

1. スキルに、見つからないときの場所を書く
2. Codex の SessionStart のフックで `~/.local/bin` にリンクを置く

サーバーが使う `codex`

1. `TORIAGE_CODEX`、PATH、ChatGPT アプリと Codex アプリの決まった場所の順に探す
2. `TORIAGE_CODEX` か PATH だけ

## 決定

4 つとも 1 にする。高橋さんが選んだ。

- 報告の `session` に、PR を作ったエージェント `agent`(`claude` か `codex`)を足す。`toriage-report` は、`CLAUDE_CODE_SESSION_ID` だけがあれば `claude`、`CODEX_THREAD_ID` だけがあれば `codex` にする。両方あるときは、どちらの会話から送ったか分からないので `session` を付けない。`agent` の無い `session` は `claude` と読む(これまでの報告と、更新前の送信スクリプト)
- 差し戻すと、サーバーは `agent` に合わせて裏で再開する。Claude Code はこれまでどおり(0011)。Codex は `<codex> exec resume -c sandbox_mode="workspace-write" -c approval_policy="on-request" -c approvals_reviewer="auto_review" <ID> <指示>` を `workdir` で起動する。出力の記録、同じ会話を重ねて動かさないこと、PATH から toriage のプラグインの `bin` を外すこと(0012)は、どちらにも同じく行う。動いているかは、`returns/<ID>.pid` のプロセスの引数に、起動した再開のコマンド(場所と指示を除く)がそのまま並んでいるかで確かめる。人が開いた `codex resume <ID>` や `claude --resume <ID>` が番号を使い回しても、動いているとみなさない
- `codex` は、`TORIAGE_CODEX`、裏に渡す PATH の `codex`、`/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex`、`/Applications/Codex.app/Contents/Resources/codex` の順に探す。見つからなければ差し戻しを断り、状態を変えない
- 裏で再開するエージェントには、Claude Code の会話の環境変数に加えて、Codex の会話の環境変数(`CODEX_THREAD_ID`、`CODEX_SESSION_ID`、`CODEX_TOOL_CALL_ID`、`CODEX_SANDBOX`、`CODEX_SANDBOX_NETWORK_DISABLED`、`PLUGIN_ROOT`、`PLUGIN_DATA`)も渡さない
- スキル `report` と `open` には、`toriage-report` や `toriage-start` が見つからないときは、その `SKILL.md` があるフォルダから見て `../../bin/` にあるものを使うと書く。Codex のサンドボックスの中では、ダッシュボードと GitHub への接続が要るので、外で動かす許可を求めると書く
- 差し戻しの指示は「スキル `toriage:report` で送り直す」にする。Claude Code と Codex のどちらでも、この名前でスキルが見える
- 画面の差し戻しの説明は、PR を作ったのが Claude か Codex かで書き分ける
- 配り方は今の `.claude-plugin` のままにする。Codex 用の `.codex-plugin` は作らない

## 理由

- Codex が Claude Code のプラグインの形をそのまま読むので、配るものを 2 つに分けずに済む
- `exec resume` は Claude の `claude -p --resume` と同じ形で、出力の記録、終わりの記録、重ねない仕組みをそのまま使える。`queue` は、同じ会話に届く代わりに、いつ終わったかが分からず、Codex アプリが動いていないと会話を開くまで動かない。まだ試してもいない
- 自動の審査は Claude の auto モードと同じ考え方で、人が見ていないところで何でもできる状態にしない。push と、作業ツリーの外にある `.git` への書き込みは、審査に任せる
- スキルに場所を書けば、利用者の場所に何も置かずに済み、フックを信頼する前から使える。Codex はスキルの一覧に `SKILL.md` の場所を出すので、そこから組み立てられる
- この PC のように、Codex をアプリだけで入れていると PATH に `codex` が無い。アプリの決まった場所も探せば、設定しなくても動く
- `agent` の無い `session` を `claude` と読めば、保存してある報告と、更新前の送信スクリプトから届く報告をそのまま差し戻せる
- 両方の変数があるのは、Claude Code の中で Codex を動かしたとき(またはその逆)で、環境変数だけでは内側がどちらか決められない。間違った会話を再開するより、差し戻しを出さないほうがよい
- サーバーを Codex のシェルから起動すると `CODEX_THREAD_ID` を持つ。そのまま裏の Claude に渡すと、送り直した報告に両方の変数が付き、2 回目の差し戻しができなくなる。逆も同じなので、両方とも渡さない

## 受け入れたトレードオフ

- Codex の会話も枝分かれし、開いている Codex アプリの画面には出ない。様子は `returns/<ID>.log` で見る
- 自動の審査が push などを断ると、裏の Codex はそこで止まる
- Claude Code の中で Codex を動かして送った報告(またはその逆)には、差し戻しが出ない
- アプリの中の `codex` の場所が変わると見つからなくなる。そのときは `TORIAGE_CODEX` を設定する。Codex アプリの場所は、ほかの人の調べた結果で、この PC では確かめていない
- Codex のフックは、利用者が信頼するまで動かない。そのあいだも、報告を送るときにダッシュボードが起動する
- Codex が、スキル `open` の `disable-model-invocation` を守るかは確かめていない
- Codex のスキルが送信スクリプトの場所を組み立てられないと、送れない。そのときは送れなかったと報告する
- 報告を送るたびに、Codex のサンドボックスの外で動かす許可が要る

## 再検討する条件

- `codex queue` で、開いている会話に届き、終わりも分かると確かめられたとき
- Codex がプラグインの `bin` を PATH に足すようになったとき
- Claude Code と Codex のほかのエージェント(Cursor など)にも対応するとき
