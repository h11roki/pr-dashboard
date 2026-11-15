# 0004. 画面の見た目は shadcn/ui に合わせ、React を使わずに CSS で写す

- ステータス: 承認
- 日付: 2026-11-15

## 文脈

- 画面は HTML と素の JavaScript の 1 ファイルで、ビルドを持たない(0001)。CSP で外のサイトのスクリプトとスタイルを読まない(0003)
- 高橋さんから、画面を shadcn/ui か Astryx の合う方でデザインするよう頼まれた
- shadcn/ui は、React、Tailwind CSS、Radix UI で書いた部品を、パッケージとしてではなくソースを自分のプロジェクトに写して使う。色は CSS 変数(`--background`、`--muted-foreground`、`--border` など)で決まる。MIT
- Astryx は Meta のデザインシステムで、React 19 以上と StyleX の上に作った部品を npm のパッケージで配る。2026-11 時点でベータ。MIT

## 検討した選択肢

1. shadcn/ui の色の変数と部品の形(Card、Badge、Tabs、Alert、Button)を素の CSS に写す
2. React と Tailwind CSS を入れ、shadcn/ui の部品をそのまま使う
3. React 19 と Astryx を入れて部品を使う
4. Astryx のテーマの色の変数だけを写す

## 決定

1 にする。

- 色は shadcn/ui の neutral の値(OKLCH)を `:root` に置き、暗い表示は `prefers-color-scheme` で切り替える
- 部品は shadcn/ui の new-york-v4 の Tailwind のクラスを読み、同じ寸法の CSS にする。状態の切り替えは Tabs、PR は Card、リスクとテストは Badge(outline に色の点)、気になる点は Alert、リンクは Button(outline)の形にする
- 状態の色(緑、黄)は shadcn/ui に無いので Tailwind の green と amber を使い、赤は shadcn/ui の `--destructive` を使う
- 書体は OS の書体を使い、Geist などを外から読まない

## 理由

- 1 なら 0001 のとおりビルドが要らず、CSP も変えずに済む。shadcn/ui はもともとソースを写して自分で持つ前提なので、写し方として素直
- 2 と 3 は Node とビルドが要り、画面 1 枚のために道具が増える。3 の部品の見た目は StyleX で書かれたコードの中にあり、CSS だけを写して使えない
- 4 は色だけで、カードやタブの形の手本にならない。ベータで値が変わりうる

## 受け入れたトレードオフ

- shadcn/ui の更新は自動では入らない。見た目を合わせ直すときは手で写す
- Radix の部品の振る舞いは持ってこない。タブは `button` と `aria-pressed`、カードの開閉は `details` で済ませる

## 再検討する条件

- 画面が増え、部品を手で写すのがつらくなったとき
- React を入れる理由がほかにできたとき
