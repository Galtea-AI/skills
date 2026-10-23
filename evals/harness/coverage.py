"""Which public CLI commands do the skills never name?

A CLI command is `galtea <tag> <x-cli-name>`, read from the OpenAPI spec the
customer's CLI syncs against. Skill text is every .md under skills/. A command
counts as named when `<noun> <verb>` appears in that text.

Usage: python3 coverage.py [--spec URL_OR_PATH] [--out coverage.json]
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

SKILLS = Path(__file__).resolve().parents[2] / "skills"


def load_spec(src: str) -> dict[str, Any]:
    if src.startswith("http"):
        with urllib.request.urlopen(src, timeout=60) as r:
            return json.load(r)
    return json.loads(Path(src).read_text())


def audit(spec: dict[str, Any], text: str) -> list[dict[str, Any]]:
    by_noun: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            if (
                not isinstance(op, dict)
                or "x-cli-name" not in op
                or op.get("x-internal")
            ):
                continue
            # An untagged operation is a top-level command: `galtea <verb>`.
            noun = (op.get("tags") or [""])[0]
            by_noun[noun].append(
                (op["x-cli-name"], method.upper(), path, op.get("summary", ""))
            )
    rows = []
    for noun, ops in sorted(by_noun.items()):
        noun_seen = (
            bool(noun) and re.search(rf"\b{re.escape(noun)}\b", text) is not None
        )
        for verb, method, path, summary in sorted(ops):
            phrase = f"{noun} {verb}" if noun else f"galtea {verb}"
            named = re.search(rf"\b{re.escape(phrase)}\b", text) is not None
            rows.append(
                {
                    "noun": noun,
                    "verb": verb,
                    "named": named,
                    "noun_seen": noun_seen,
                    "method": method,
                    "path": path,
                    "summary": summary,
                }
            )
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", default="https://api.galtea.ai/openapi.json")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    text = "\n".join(p.read_text() for p in SKILLS.rglob("*.md")).lower()
    rows = audit(load_spec(a.spec), text)
    named = sum(r["named"] for r in rows)
    print(
        f"public CLI commands: {len(rows)}; named in the skills: {named}; never named: {len(rows) - named}\n"
    )
    for noun in sorted({r["noun"] for r in rows if r["noun"] and not r["noun_seen"]}):
        print(
            f"noun never mentioned: {noun}: {', '.join(r['verb'] for r in rows if r['noun'] == noun)}"
        )
    print()
    for r in rows:
        if r["noun_seen"] and not r["named"]:
            print(
                f"never named: galtea {r['noun']} {r['verb']}  [{r['method']} {r['path']}]  {r['summary']}"
            )
    if a.out:
        a.out.write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
