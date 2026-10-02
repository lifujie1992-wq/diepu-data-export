#!/usr/bin/env bash
# 在当前网络环境下把本仓库推送到 GitHub。
#
# 背景：本机 github.com:443 被墙（超时），普通 `git push` 用不了；
#       但 api.github.com 通，所以走 GitHub 的 Git Data API 推。
#
# 用法:
#   ./push.sh                  # 用 HEAD 提交推送到 main
#   GITHUB_REPO=owner/name ./push.sh
#
# 前置: gh 已登录（`gh auth login`），且已有至少一次提交。
set -euo pipefail
cd "$(dirname "$0")"

OWNER_REPO="${GITHUB_REPO:-lifujie1992-wq/diepu-data-export}"
BRANCH="${GITHUB_BRANCH:-main}"

TOKEN="$(gh auth token)"
[ -n "$TOKEN" ] || { echo "拿不到 gh token，先 gh auth login"; exit 1; }

echo "→ 目标仓库: $OWNER_REPO  分支: $BRANCH"
GH_TOKEN="$TOKEN" python3 - "$OWNER_REPO" "$BRANCH" <<'PYEOF'
import base64, json, os, subprocess, sys, urllib.error, urllib.request

OWNER_REPO, BRANCH = sys.argv[1], sys.argv[2]
TOKEN = os.environ["GH_TOKEN"]
API = f"https://api.github.com/repos/{OWNER_REPO}"


def call(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "diepu-push")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code} {method} {url}\n  {e.read().decode()[:400]}", file=sys.stderr)
        raise


files = subprocess.run(["git", "ls-files"], capture_output=True, text=True).stdout.split()
msg = subprocess.run(["git", "log", "-1", "--pretty=%B"], capture_output=True, text=True).stdout.strip()

# 远端当前 HEAD（可能还不存在）
try:
    parent = call("GET", f"{API}/git/ref/heads/{BRANCH}")["object"]["sha"]
    base_tree = call("GET", f"{API}/git/commits/{parent}")["tree"]["sha"]
except urllib.error.HTTPError:
    parent = base_tree = None

if parent is None:
    # 空仓库：Git Data API 建不了 blob，先用 Contents API 种初始提交
    seed = files[0]
    call("PUT", f"{API}/contents/{seed}", {
        "message": "chore: init",
        "content": base64.b64encode(open(seed, "rb").read()).decode(),
        "branch": BRANCH,
    })
    parent = call("GET", f"{API}/git/ref/heads/{BRANCH}")["object"]["sha"]
    base_tree = call("GET", f"{API}/git/commits/{parent}")["tree"]["sha"]

print(f"  远端 HEAD={parent[:8]}  文件 {len(files)} 个")

tree = []
for f in files:
    content = open(f, "rb").read()
    blob = call("POST", f"{API}/git/blobs",
                {"content": base64.b64encode(content).decode(), "encoding": "base64"})
    tree.append({"path": f, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    print(f"    {f} ({len(content)} B)")

t = call("POST", f"{API}/git/trees", {"base_tree": base_tree, "tree": tree})
c = call("POST", f"{API}/git/commits", {"message": msg, "tree": t["sha"], "parents": [parent]})
call("PATCH", f"{API}/git/refs/heads/{BRANCH}", {"sha": c["sha"], "force": True})
print(f"✓ 已推送 commit {c['sha'][:8]} 到 {BRANCH}")
PYEOF
