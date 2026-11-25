#!/usr/bin/env python3
"""Read-only review of the TradingDesk PR range (#19..#26). Uses git only. No network, no checkout.

Run: python3 scripts/qa/diff_review.py [--base 0307f61] [--head 91d3c38] [--show-diff]

Reports, for every PR commit in the range, which of the reviewed files changed, then compares the order surface
at BASE and HEAD:
  - the broker client's write call sites (method + path), read from threecommas.py with ast
  - the worker's POST routes, read from httpapi.py
  - the browser's write call sites and /api/* literals, read from web/src
BASE is the commit before #19 (#18, 0307f61). HEAD is #26 (91d3c38). Exit status 1 when any surface differs.
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys

REVIEWED = ["src/warp3c/engine.py", "src/warp3c/risk.py", "src/warp3c/threecommas.py", "src/warp3c/config.py",
            "src/warp3c/httpapi.py", "fly.toml"]
ALSO_FORBIDDEN = ["Dockerfile", ".github"]


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def show(rev: str, path: str) -> str:
    return git("show", f"{rev}:{path}")


def text(node):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(part.value if isinstance(part, ast.Constant) else "{}" for part in node.values)
    return None


def broker_writes(rev: str) -> list[tuple[str, str]]:
    found = []
    for node in ast.walk(ast.parse(show(rev, "src/warp3c/threecommas.py"))):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.args \
                and node.func.attr in {"request", "_bot_write"} and text(node.args[0]) in {"POST", "PUT", "PATCH", "DELETE"}:
            found.append((text(node.args[0]), str(text(node.args[1]))))
    return sorted(found)


def post_routes(rev: str) -> list[str]:
    return sorted(set(re.findall(r'method == "POST" and clean == "(/[^"]+)"', show(rev, "src/warp3c/httpapi.py"))))


def worker_routes(rev: str) -> set[str]:
    for node in ast.walk(ast.parse(show(rev, "src/warp3c/operator.py"))):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "ROUTES" for t in node.targets):
            return {elt.value for elt in node.value.elts}
    raise SystemExit("ROUTES not found in operator.py")


def browser_surface(rev: str) -> tuple[list[str], int, list[str]]:
    literals, writes, fetchers = set(), 0, []
    names = git("ls-tree", "-r", "--name-only", rev, "web/src").split()
    for name in names:
        if not name.endswith((".ts", ".tsx")):
            continue
        body = show(rev, name)
        literals.update(re.findall(r'"(/api/[A-Za-z0-9/_-]+)"', body))
        writes += len(re.findall(r'method:\s*"(?:POST|PUT|PATCH|DELETE)"', body))
        if re.search(r"\bfetch\(", body) and not name.endswith("/api.ts"):
            fetchers.append(name)
    return sorted(literals), writes, fetchers


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="0307f61")
    parser.add_argument("--head", default="91d3c38")
    parser.add_argument("--show-diff", action="store_true")
    args = parser.parse_args()
    base, head = args.base, args.head
    print(f"range {base}..{head}")
    commits = git("log", "--reverse", "--format=%h %s", f"{base}..{head}").strip().splitlines()
    print(f"{len(commits)} commits")
    for line in commits:
        sha = line.split()[0]
        changed = git("diff", "--name-only", f"{sha}~1", sha, "--", *REVIEWED, *ALSO_FORBIDDEN).split()
        print(f"  {line}\n      reviewed/forbidden files changed: {', '.join(changed) if changed else 'none'}")
    print("\nper-file stat over the whole range")
    for path in REVIEWED + ALSO_FORBIDDEN:
        stat = git("diff", "--shortstat", base, head, "--", path).strip()
        print(f"  {path:<28} {stat or 'unchanged'}")
    print("\nall src/ files changed in the range")
    print(git("diff", "--stat", base, head, "--", "src").rstrip())
    if args.show_diff:
        print(git("diff", base, head, "--", *REVIEWED))

    same = True
    print("\norder surface, BASE vs HEAD")
    for label, getter in (("broker write call sites", broker_writes), ("worker POST routes", post_routes)):
        before, after = getter(base), getter(head)
        verdict = "identical" if before == after else "DIFFERENT"
        same &= before == after
        print(f"  {label}: {verdict} ({len(before)} items)")
        if before != after:
            print(f"    base: {before}\n    head: {after}")
    (lit_a, writes_a, fetch_a), (lit_b, writes_b, fetch_b) = browser_surface(base), browser_surface(head)
    same &= writes_a == writes_b and fetch_a == fetch_b == []
    print(f"  browser write call sites: {writes_a} at base, {writes_b} at head; fetch() outside api.ts: {fetch_b or 'none'}")
    added = sorted(set(lit_b) - set(lit_a))
    removed = sorted(set(lit_a) - set(lit_b))
    allowed = worker_routes(head) | set(post_routes(head)) | {"/api/session", "/api/stream", "/api/rules"}
    unknown = [item for item in added if item not in allowed]
    same &= not unknown
    print(f"  browser /api/* targets added: {added or 'none'}; removed: {removed or 'none'}")
    print(f"    added targets that are not an existing worker route: {unknown or 'none'}")
    print("\nverdict: " + ("no new order path: write surface is identical at both ends" if same else "SURFACE CHANGED"))
    return 0 if same else 1


if __name__ == "__main__":
    sys.exit(main())
