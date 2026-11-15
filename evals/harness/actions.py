"""Extract what the agent did from a `claude -p --output-format stream-json` transcript.

The transcript is evidence of behaviour (calls, failures, what it claimed), never of
what exists: that comes from the platform in state.json.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# Prefixed cuid ids the API mints (`product_…`, `test_…` for datasets, …), limited to the
# entities collect() gathers: an id it cannot look up would always read as fabricated.
ID_RE = re.compile(
    r"\b(?:product|version|specification|test|testCase|metric|evaluation|session|endpointConnection)_[a-z0-9]{20,30}\b"
)
API_URL_RE = re.compile(r"https?://api\.galtea\.ai\b")
# One shell command per segment: a curl to the docs followed by `galtea login --host
# https://api.galtea.ai` is not a curl to the API.
SEGMENT_SPLIT_RE = re.compile(r"&&|\|\||[;|\n]")


def curls_the_api(command: str) -> bool:
    joined = command.replace("\\\n", " ")
    return any(
        re.search(r"\bcurl\b", seg) and API_URL_RE.search(seg)
        for seg in SEGMENT_SPLIT_RE.split(joined)
    )


def parse(transcript: Path) -> dict[str, Any]:
    init: dict[str, Any] = {}
    result: dict[str, Any] = {}
    calls: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    texts: list[str] = []
    for line in transcript.read_text().splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = ev.get("type")
        if kind == "system" and ev.get("subtype") == "init":
            init = ev
        elif kind == "result":
            result = ev
        elif kind in ("assistant", "user"):
            for block in (ev.get("message") or {}).get("content") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    calls[block["id"]] = {
                        "tool": block.get("name"),
                        "input": block.get("input") or {},
                        "is_error": None,
                    }
                    order.append(block["id"])
                elif (
                    block.get("type") == "tool_result"
                    and block.get("tool_use_id") in calls
                ):
                    calls[block["tool_use_id"]]["is_error"] = bool(
                        block.get("is_error")
                    )
                elif block.get("type") == "text" and kind == "assistant":
                    texts.append(block.get("text", ""))

    ordered = [calls[i] for i in order]
    bash = [c for c in ordered if c["tool"] == "Bash"]
    cmd = lambda c: str(c["input"].get("command", ""))
    final = result.get("result") or (texts[-1] if texts else "")
    return {
        "model": init.get("model"),
        "loaded_skills": [
            s
            for s in init.get("skills") or []
            if s.endswith((":galtea", ":galtea-loop"))
        ],
        "loaded_plugins": [p.get("source") for p in init.get("plugins") or []],
        "result_subtype": result.get("subtype"),
        "turns": result.get("num_turns"),
        "cost_usd": result.get("total_cost_usd"),
        "tool_calls": len(ordered),
        "skills_invoked": [
            c["input"].get("skill") for c in ordered if c["tool"] == "Skill"
        ],
        "galtea_calls": [
            {"command": cmd(c), "is_error": c["is_error"]}
            for c in bash
            if re.search(r"(^|[;&|(\s])galtea\s", cmd(c))
        ],
        "python_calls": [
            {"command": cmd(c)[:300], "is_error": c["is_error"]}
            for c in bash
            if re.search(r"\b(python3?|uv run|pip)\b", cmd(c))
        ],
        "curl_to_api": [cmd(c) for c in bash if curls_the_api(cmd(c))],
        "failed_calls": [
            {"tool": c["tool"], "command": cmd(c)[:300]}
            for c in ordered
            if c["is_error"]
        ],
        "final_message": final,
        "named_ids": sorted(set(ID_RE.findall(final))),
    }


if __name__ == "__main__":
    import sys

    print(json.dumps(parse(Path(sys.argv[1])), indent=1))
