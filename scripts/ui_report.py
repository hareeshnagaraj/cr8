"""Format one page's ui_audit.js result. Reads the raw browse output on stdin."""

from __future__ import annotations

import json
import sys

MARK = {"high": "HIGH", "medium": "med ", "low": "low "}


def main() -> int:
    raw = sys.stdin.read().strip()
    start = raw.find("{")
    if start < 0:
        print("  (no result)")
        return 0
    body = raw[start:]
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        try:
            data = json.loads(json.loads(f'"{body}"'))
        except Exception:
            print("  (unparseable result)")
            return 0
    if isinstance(data, str):
        data = json.loads(data)

    counts = data["counts"]
    print(
        f"\n## {data['url']}  —  {counts['high']} high, "
        f"{counts['medium']} medium, {counts['low']} low  "
        f"({data['nodes']} nodes)"
    )
    if not data["findings"]:
        print("  clean")
    for finding in data["findings"]:
        mark = MARK[finding["severity"]]
        where = f"  <- {finding['el']}" if finding.get("el") else ""
        print(f"  [{mark}] {finding['rule']}: {finding['detail']}{where}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
