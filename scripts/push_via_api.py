"""通过 GitHub REST API 推送提交（当 github.com:443 不可达、api.github.com 可用时使用）。

用法：python scripts/push_via_api.py [owner/repo]
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.version import REPO_NAME, REPO_OWNER  # noqa: E402


def git(*args: str, binary: bool = False):
    result = subprocess.run(["git", *args], capture_output=True, check=True)
    return result.stdout if binary else result.stdout.decode("utf-8", "replace")


def gh_api(method: str, path: str, payload: dict | None = None) -> dict:
    cmd = ["gh", "api", "--method", method, path]
    if payload is not None:
        cmd += ["--input", "-"]
    proc = subprocess.run(
        cmd, input=json.dumps(payload) if payload else None, capture_output=True, text=True, encoding="utf-8"
    )
    if proc.returncode != 0:
        raise SystemExit(f"gh api 失败：{method} {path}\n{proc.stderr}")
    return json.loads(proc.stdout) if proc.stdout.strip() else {}


def split_identity(text: str) -> tuple[str, str, str]:
    name, email = text.split(" <", 1)
    email, raw_date = email.split("> ", 1)
    timestamp, _, offset = raw_date.strip().partition(" ")
    sign = -1 if offset.startswith("-") else 1
    tz = timezone(sign * timedelta(hours=int(offset[1:3]), minutes=int(offset[3:5])))
    return name, email, datetime.fromtimestamp(int(timestamp), tz).isoformat()


def main(argv: list[str] | None = None) -> int:
    repo = (argv or sys.argv[1:] or [f"{REPO_OWNER}/{REPO_NAME}"])[0]
    head = git("rev-parse", "HEAD").strip()
    raw = git("cat-file", "-p", head)
    headers, _, message = raw.partition("\n\n")
    parent = tree_sha = ""
    author_line = committer_line = ""
    for line in headers.splitlines():
        if line.startswith("tree "):
            tree_sha = line[5:].strip()
        elif line.startswith("parent "):
            parent = line[7:].strip()
        elif line.startswith("author "):
            author_line = line[7:]
        elif line.startswith("committer "):
            committer_line = line[10:]
    if not parent:
        raise SystemExit("初始提交不支持通过 API 推送，请使用 git push")

    remote_head = gh_api("GET", f"repos/{repo}/git/ref/heads/main")["object"]["sha"]
    if remote_head == head:
        print("远端已是最新，无需推送")
        return 0
    if remote_head != parent:
        raise SystemExit(f"远端 {remote_head} 与本地父提交 {parent} 不一致，需人工处理")

    changed = [line.split("\t") for line in git("diff", "--name-status", parent, head).strip().splitlines()]
    entries = []
    for status, path in changed:
        if status.startswith("D"):
            print("跳过删除项（Git Data API 不支持在 tree 中直接删除）：", path)
            raise SystemExit("包含删除操作，请改用 git push")
        data = git("show", f"{head}:{path}", binary=True)
        blob = gh_api(
            "POST",
            f"repos/{repo}/git/blobs",
            {"content": base64.b64encode(data).decode("ascii"), "encoding": "base64"},
        )
        mode_line = git("ls-tree", head, "--", path).split()
        entries.append(
            {
                "path": path,
                "mode": mode_line[0] if mode_line else "100644",
                "type": "blob",
                "sha": blob["sha"],
            }
        )
        print(f"  blob {path} -> {blob['sha'][:10]}")

    parent_tree = gh_api("GET", f"repos/{repo}/git/commits/{parent}")["tree"]["sha"]
    new_tree = gh_api("POST", f"repos/{repo}/git/trees", {"base_tree": parent_tree, "tree": entries})
    if new_tree["sha"] != tree_sha:
        raise SystemExit(f"tree 校验失败：{new_tree['sha']} != {tree_sha}")

    a_name, a_email, a_date = split_identity(author_line)
    c_name, c_email, c_date = split_identity(committer_line)
    commit = gh_api(
        "POST",
        f"repos/{repo}/git/commits",
        {
            "message": message,
            "tree": new_tree["sha"],
            "parents": [parent],
            "author": {"name": a_name, "email": a_email, "date": a_date},
            "committer": {"name": c_name, "email": c_email, "date": c_date},
        },
    )
    gh_api("PATCH", f"repos/{repo}/git/refs/heads/main", {"sha": commit["sha"], "force": False})
    final = gh_api("GET", f"repos/{repo}/git/ref/heads/main")["object"]["sha"]
    print(f"远端 main：{final}")
    print("与本地 HEAD 一致：" + ("是" if final == head else "否"))
    return 0 if final == head else 1


if __name__ == "__main__":  # pragma: no cover - 脚本入口
    raise SystemExit(main())
