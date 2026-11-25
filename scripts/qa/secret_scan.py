#!/usr/bin/env python3
"""Read-only secret scan for built assets, committed screenshots and logs. Makes no network call.

Run: python3 scripts/qa/secret_scan.py [--root DIR ...] [--json]

Method. Every file under the roots is read as bytes, plus a text decode with errors ignored. Three detectors:
  1. Named markers: the env var names that hold credentials, and "Bearer <token>" strings.
  2. Token-shaped literals: JWTs, vendor key prefixes, long hex or base64 runs next to a credential word.
  3. Embedded text in images: webp/png/jpg metadata chunks (EXIF/XMP/tEXt) are scanned as text like any other bytes.
A match reports file, detector and a redacted preview. The matched secret is never printed in full.
Exit status is 1 when there is any finding that is not in ALLOWED.

Allowed findings are names, not values: the dashboard bundle must be able to say "operator token" in the login
form, and the font licences contain no key material. Each entry says why it is allowed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# Artifacts get every detector. Source roots get the value-shaped detectors only, because source and docs are
# allowed to name an env var. They are scanned so that a pasted value is caught before it is committed.
ARTIFACT_ROOTS = ["web/dist", "docs/dashboard/screenshots"]
SOURCE_ROOTS = ["docs", "web/qa", "web/src", "tests", "scripts"]
NAME_ONLY = {"env-name", "api-key-word"}
SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".ruff_cache"}

MARKERS = [
    ("env-name", re.compile(rb"WARP_OPERATOR_TOKEN|WARP_SESSION_SECRET|WARP_LIVE_EXECUTION_ACK|THREECOMMAS_API_SECRET|THREECOMMAS_API_KEY")),
    ("api-key-word", re.compile(rb"API_KEY|API_SECRET|\bSECRET\b", re.I)),
    ("bearer", re.compile(rb"Bearer\s+[A-Za-z0-9._~+/=-]{8,}")),
    ("jwt", re.compile(rb"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("vendor-key", re.compile(rb"\b(?:sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10,}|nvapi-[A-Za-z0-9_-]{20,})")),
    ("private-key", re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("assigned-secret", re.compile(rb"(?:token|secret|password|passwd|api[_-]?key)[\"']?\s*[:=]\s*[\"'][A-Za-z0-9+/_=-]{20,}[\"']", re.I)),
    ("cookie-value", re.compile(rb"warp_session=[A-Za-z0-9._-]{16,}")),
]

# (path suffix, detector, reason). An entry matches a finding only when file and detector both match.
ALLOWED: list[tuple[str, str, str]] = [
    ("web/dist/assets/", "env-name", "login and mode help text from web/src/i18n.ts names WARP_DASHBOARD_PASSWORD, "
     "WARP_OPERATOR_TOKEN and the live-execution acknowledgement variable. Names only, no value."),
    ("tests/test_phase1.py", "bearer", "pre-existing unit-test fixture 'operator-token-16', a constant that only "
     "exists inside the test process"),
    ("tests/test_phase1.py", "assigned-secret", "pre-existing unit-test fixture login password for the in-process test server"),
    ("tests/test_phase2.py", "bearer", "pre-existing unit-test fixture 'operator-token-16'"),
]


def preview(data: bytes, match: re.Match) -> str:
    text = match.group(0).decode("latin-1")
    if len(text) <= 12:
        return text
    return f"{text[:6]}…[{len(text)} chars]"


def scan_file(path: Path, *, names: bool = True) -> list[dict]:
    data = path.read_bytes()
    found = []
    for name, pattern in MARKERS:
        if not names and name in NAME_ONLY:
            continue
        for match in pattern.finditer(data):
            line = data.count(b"\n", 0, match.start()) + 1
            found.append({"file": str(path.relative_to(REPO)), "detector": name, "line": line, "preview": preview(data, match)})
    return found


def walk(root: Path):
    if root.is_file():
        yield root
        return
    for path in sorted(root.rglob("*")):
        if path.is_file() and not (set(path.relative_to(root).parts) & SKIP_DIRS):
            yield path


def allowed(finding: dict) -> str | None:
    for suffix, detector, reason in ALLOWED:
        if suffix in finding["file"] and finding["detector"] == detector:
            return reason
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--root", action="append", help="directory or file, relative to the repo root")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--self-test", action="store_true", help="prove the detectors fire on synthetic values")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    plan = [(name, True) for name in args.root] if args.root else (
        [(name, True) for name in ARTIFACT_ROOTS] + [(name, False) for name in SOURCE_ROOTS])
    roots = [name for name, _ in plan]
    scanned, findings, missing = 0, [], []
    for name, names in plan:
        root = REPO / name
        if not root.exists():
            missing.append(name)
            continue
        for path in walk(root):
            if path.resolve() == Path(__file__).resolve() or "p5_secret_sentinel" in path.name:
                continue
            scanned += 1
            findings.extend(scan_file(path, names=names))
    unexplained = [item for item in findings if not allowed(item)]
    explained = [dict(item, reason=allowed(item)) for item in findings if allowed(item)]
    report = {"roots": roots, "missing_roots": missing, "files_scanned": scanned, "findings": len(findings),
              "explained": len(explained), "unexplained": unexplained}
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"scanned {scanned} files under {', '.join(roots)}" + (f" (absent: {', '.join(missing)})" if missing else ""))
        print(f"findings: {len(findings)}, explained: {len(explained)}, unexplained: {len(unexplained)}")
        for reason in sorted({item["reason"] for item in explained}):
            hits = [item for item in explained if item["reason"] == reason]
            print(f"  explained x{len(hits)} in {sorted({h['file'] for h in hits})}: {reason}")
        for item in unexplained:
            print(f"  {item['file']}:{item['line']} [{item['detector']}] {item['preview']}")
    return 1 if unexplained else 0


def self_test() -> int:
    samples = {
        "env-name": b"x WARP_SESSION_SECRET y",
        "bearer": b"Authorization: Bearer abcdefghijklmnop1234",
        "jwt": b"eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijk",
        "vendor-key": b"k=sk-" + b"A" * 24,
        "assigned-secret": b'password: "' + b"Z" * 24 + b'"',
        "cookie-value": b"warp_session=" + b"q" * 20,
    }
    failed = [name for name, text in samples.items() if not any(n == name and p.search(text) for n, p in MARKERS)]
    if failed:
        print("detectors that did not fire:", ", ".join(failed))
        return 1
    print(f"self-test ok: {len(samples)} synthetic samples detected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
