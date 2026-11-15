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

root="$(CDPATH='' cd -- "$(dirname -- "$(readlink -f -- "$0")")/.." && pwd -P)"
dashboard="${PR_DASHBOARD_URL:-http://127.0.0.1:8787}"
inbox="${PR_DASHBOARD_INBOX:-$root/inbox}"

judgment="$(cat)"
jq -e 'type == "object"' >/dev/null 2>&1 <<<"$judgment" ||
  fail "判断材料が JSON のオブジェクトとして読めない"
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

problem="$(python3 -c '
import json
import sys

sys.path.insert(0, sys.argv[1])
from server import InvalidReport, parse_report

try:
    parse_report(json.load(sys.stdin))
except InvalidReport as error:
    print(error)
' "$root" <<<"$body")"
[ -z "$problem" ] || fail "報告が検証に通らない: $problem"

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

if jq -e '.saved' "$response" >/dev/null; then
  echo "ダッシュボードに送った: $label"
else
  echo "ダッシュボードには $label のもっと新しい報告があるので、置き換えなかった" >&2
fi
