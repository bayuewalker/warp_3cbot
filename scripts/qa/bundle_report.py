#!/usr/bin/env python3
"""Bundle weight table for web/dist: first-load versus lazy, raw and gzip. Read-only, no network.

Run: python3 scripts/qa/bundle_report.py [--dist web/dist] [--expect-index-kb 352.93] [--json]

First-load = the files named by index.html (script, stylesheet, modulepreload). Everything else under assets/ is
lazy or on demand. The server sends these uncompressed (see RELEASE_CANDIDATE_REPORT.md), so raw bytes are what
a phone downloads today. gzip is what a compressing proxy would send.
Two size columns for text files. "kB" is the file size in bytes / 1000, which is what is transferred.
"vite kB" is the character count / 1000, which is what `vite build` prints (it differs when a bundle holds
non-ASCII text). Vite's gzip figure uses its own method, so gzip here (zlib level 9) will not match it exactly.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def kb(size: int) -> float:
    return round(size / 1000, 2)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", default="web/dist")
    parser.add_argument("--expect-index-kb", type=float)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    dist = REPO / args.dist
    html = (dist / "index.html").read_text()
    first = {m.lstrip("/") for m in re.findall(r'(?:src|href)="(/assets/[^"]+)"', html)}
    rows = []
    for path in sorted(p for p in (dist / "assets").rglob("*") if p.is_file()):
        data = path.read_bytes()
        rel = path.relative_to(dist).as_posix()
        kind = path.suffix.lstrip(".")
        if kind == "js" and rel in first:
            role = "first-load"
        elif kind == "js":
            role = "lazy"
        elif kind == "css":
            role = "first-load" if rel in first else "lazy"
        elif kind == "woff2":
            role = "font (on demand)"
        else:
            role = "image/other (on demand)"
        rows.append({"file": rel, "type": kind, "role": role, "bytes": len(data), "kB": kb(len(data)),
                     "vite_kB": kb(len(data.decode("utf-8"))) if kind in {"js", "css"} else None,
                     "gzip_kB": kb(len(gzip.compress(data, 9)))})
    totals = {}
    for kind in ("js", "css"):
        for role in ("first-load", "lazy"):
            picked = [r for r in rows if r["type"] == kind and r["role"] == role]
            if picked:
                totals[f"{kind} {role}"] = {"kB": round(sum(r["kB"] for r in picked), 2),
                                            "gzip_kB": round(sum(r["gzip_kB"] for r in picked), 2)}
    report = {"dist": args.dist, "first_load_files": sorted(first), "files": rows, "totals": totals}
    ok = True
    if args.expect_index_kb is not None:
        index = [r for r in rows if r["type"] == "js" and r["role"] == "first-load"]
        measured = round(sum(r["vite_kB"] for r in index), 2)
        ok = abs(measured - args.expect_index_kb) < 0.005
        report["expected_index_js_kB"] = args.expect_index_kb
        report["measured_index_js_kB"] = measured
        report["index_js_matches"] = ok
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"{'file':<58}{'role':<26}{'kB':>9}{'vite kB':>9}{'gzip kB':>9}")
        for r in rows:
            vite = "" if r["vite_kB"] is None else f"{r['vite_kB']:.2f}"
            print(f"{r['file']:<58}{r['role']:<26}{r['kB']:>9.2f}{vite:>9}{r['gzip_kB']:>9.2f}")
        for name, value in totals.items():
            print(f"{'TOTAL ' + name:<84}{value['kB']:>9.2f}{'':>9}{value['gzip_kB']:>9.2f}")
        if "index_js_matches" in report:
            print(f"first-load index JS {report['measured_index_js_kB']} kB (vite count) vs expected {args.expect_index_kb} kB: "
                  + ("match" if ok else "MISMATCH"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
