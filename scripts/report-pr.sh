#!/usr/bin/env bash
set -euo pipefail

if [ $# -ne 1 ]; then
  echo "使い方: report-pr.sh <PR番号> < 判断材料.json" >&2
  exit 2
fi

pr="$1"
dashboard="${PR_DASHBOARD_URL:-http://127.0.0.1:8787}"
inbox="${PR_DASHBOARD_INBOX:-$(cd "$(dirname "$0")/.." && pwd)/inbox}"
judgment="$(cat)"
facts="$(gh pr view "$pr" --json number,url,title,headRefName,additions,deletions,changedFiles)"
repo="$(gh repo view --json nameWithOwner -q .nameWithOwner)"

body="$(jq -n --argjson judgment "$judgment" --argjson facts "$facts" --arg repo "$repo" '
  $judgment + {
    repo: $repo,
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

response="$(mktemp)"
trap 'rm -f "$response"' EXIT
status="$(curl -sS --max-time 10 -o "$response" -w '%{http_code}' -X POST "$dashboard/api/prs" \
  -H 'Content-Type: application/json' --data-binary @- <<<"$body")" || status="000"

case "$status" in
  200)
    echo "ダッシュボードに送った: $repo#$pr"
    ;;
  000)
    mkdir -p "$inbox"
    printf '%s\n' "$body" >"$inbox/${repo//\//_}-$pr.json"
    echo "ダッシュボードにつながらないので $inbox に保存した。次に起動したときに取り込まれる" >&2
    ;;
  *)
    echo "ダッシュボードに断られた($status): $(cat "$response")" >&2
    exit 1
    ;;
esac
