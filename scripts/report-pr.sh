#!/usr/bin/env bash
set -euo pipefail

if [ $# -ne 1 ]; then
  echo "使い方: report-pr.sh <PR番号> < 判断材料.json" >&2
  exit 2
fi

fail() {
  echo "$1" >&2
  exit 1
}

script="$(readlink -f -- "$0")" || fail "スクリプトの置き場所が分からない"
root="$(CDPATH='' cd -- "$(dirname -- "$script")/.." && pwd -P)"
dashboard="${PR_DASHBOARD_URL:-http://127.0.0.1:8787}"
dashboard="${dashboard%/}"
inbox="${PR_DASHBOARD_INBOX:-$root/inbox}"

judgment="$(cat)"
jq -e -s 'length == 1 and (.[0] | type == "object")' >/dev/null 2>&1 <<<"$judgment" ||
  fail "判断材料が JSON のオブジェクト 1 つとして読めない"
facts="$(gh pr view "$1" --json number,url,title,headRefName,additions,deletions,changedFiles)" ||
  fail "gh で PR $1 を読めなかった"

body="$(jq -n --argjson judgment "$judgment" --argjson facts "$facts" '
  $judgment + {
    repo: ($facts.url | split("/") | "\(.[3])/\(.[4])"),
    number: $facts.number,
    url: $facts.url,
    title: $facts.title,
    branch: $facts.headRefName,
    changes: {
      files: $facts.changedFiles,
      additions: $facts.additions,
      deletions: $facts.deletions
    }
  }')"
label="$(jq -r '"\(.repo)#\(.number)"' <<<"$body")"
name="$(jq -r '"\(.repo | sub("/"; "_"))-\(.number)"' <<<"$body")"

problem="$(python3 -I "$root/server.py" --check 2>&1 <<<"$body")" ||
  fail "報告が検証に通らない: $problem"

response="$(mktemp)"
trap 'rm -f "$response"' EXIT
curl_exit=0
status="$(curl -sS --noproxy '*' --max-time 10 -o "$response" -w '%{http_code}' \
  -X POST "$dashboard/api/prs" -H 'Content-Type: application/json' \
  --data-binary @- <<<"$body")" || curl_exit=$?

if [ "$curl_exit" -eq 7 ]; then
  mkdir -p "$inbox"
  printf '%s\n' "$body" >"$inbox/$name.json"
  echo "ダッシュボードにつながらないので $inbox/$name.json に保存した。次に起動したときに取り込まれる" >&2
  exit 0
fi
[ "$curl_exit" -eq 0 ] || fail "ダッシュボードに届いたか分からない(curl $curl_exit)。送り直してよい"
[ "$status" = "200" ] || fail "ダッシュボードに断られた($status): $(cat "$response")"

saved="$(jq -r '.saved' "$response" 2>/dev/null)" || saved=""
case "$saved" in
  true)
    echo "ダッシュボードに送った: $label"
    ;;
  false)
    echo "ダッシュボードには $label のもっと新しい報告があるので、置き換えなかった" >&2
    ;;
  *)
    fail "ダッシュボードの応答が読めない: $(cat "$response")"
    ;;
esac
