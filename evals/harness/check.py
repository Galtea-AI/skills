"""Level 1: compare a case's `expect` against state.json, plus the how-was-it-made checks.

No model, free, repeatable. Each failed check reports its actual and expected value;
there is no aggregate score on purpose.

Assertion vocabulary (kept small on purpose):
  count: 3 | ">=3" | "<=0"
  each:  {field: value} | {field: {in: [...]}} | {generated: true} | {from_specification: true}
  links: every_specification_has_metrics, every_specification_has_dataset
  final_message_matches: <regex>
  commands_include: [<regex>, ...]   (each matches at least one `galtea` call)
  product: true | false   (omit it when either outcome is fine)

`validate_case` rejects any other key, an entity collect() never returns, and a count it
cannot parse, before an agent run spends anything. An `each` rule over no rows fails:
a typo or an empty result must not read as a pass.

`generated` reads `isExtendable`: the API sets it only for a dataset whose cases a
generator produced (`Test.isGenerated()`: `taskId` is set). `specificationId` is not
proof, because an uploaded dataset can carry one too.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from collect import ENTITIES

RULE_KEYS = ("product", "links", "final_message_matches", "commands_include")
COUNT_RE = re.compile(r"^(>=|<=)?\s*\d+$")


def validate_case(case: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key, rule in (case.get("expect") or {}).items():
        if key in RULE_KEYS:
            continue
        if key not in ENTITIES:
            errors.append(
                f"unknown expect key {key!r}; entities are {', '.join(ENTITIES)}"
            )
            continue
        if "count" in rule and not COUNT_RE.match(str(rule["count"]).strip()):
            errors.append(f"{key}.count {rule['count']!r}: use n, '>=n' or '<=n'")
    return errors


def _count_ok(n: int, spec: Any) -> bool:
    s = str(spec).strip()
    for op in (">=", "<="):
        if s.startswith(op):
            v = int(s[len(op) :])
            return n >= v if op == ">=" else n <= v
    return n == int(s)


def _field_ok(row: dict[str, Any], field: str, want: Any) -> bool:
    if field == "generated":
        return bool(row.get("isExtendable")) is bool(want)
    if field == "from_specification":
        return (row.get("specificationId") is not None) is bool(want)
    got = row.get(field)
    if isinstance(want, dict) and "in" in want:
        return got in want["in"]
    return got == want


def check(
    case: dict[str, Any], state: dict[str, Any], actions: dict[str, Any], arm: str
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    add = lambda name, ok, actual=None, expected=None: out.append(
        {"check": name, "passed": bool(ok), "actual": actual, "expected": expected}
    )

    expect = case.get("expect") or {}
    if "product" in expect:
        names = [p.get("name") for p in state.get("products") or []]
        add(
            "product_created",
            bool(names) is bool(expect["product"]),
            names,
            expect["product"],
        )
    for entity, rule in expect.items():
        if entity in RULE_KEYS:
            continue
        rows = state.get(entity) or []
        if "count" in rule:
            add(
                f"{entity}.count",
                _count_ok(len(rows), rule["count"]),
                len(rows),
                rule["count"],
            )
        for field, want in (rule.get("each") or {}).items():
            if not rows:
                add(f"{entity}.each.{field}", False, {"no_rows": entity}, want)
                continue
            bad = [r.get("id") for r in rows if not _field_ok(r, field, want)]
            add(f"{entity}.each.{field}", not bad, {"failing_ids": bad}, want)

    links = expect.get("links") or {}
    specs = state.get("specifications") or []
    if links.get("every_specification_has_metrics"):
        bad = [s["id"] for s in specs if not s.get("metricIds")]
        add(
            "links.every_specification_has_metrics",
            not bad,
            {"specs_without_metrics": bad},
            True,
        )
    if links.get("every_specification_has_dataset"):
        linked = {d.get("specificationId") for d in state.get("datasets") or []}
        bad = [s["id"] for s in specs if s["id"] not in linked]
        add(
            "links.every_specification_has_dataset",
            not bad,
            {"specs_without_dataset": bad},
            True,
        )
    if "final_message_matches" in expect:
        pat = expect["final_message_matches"]
        add(
            "final_message_matches",
            re.search(pat, actions.get("final_message") or "") is not None,
            None,
            pat,
        )

    commands = [c["command"] for c in actions.get("galtea_calls") or []]
    for pat in expect.get("commands_include") or []:
        add(
            f"commands_include:{pat}",
            any(re.search(pat, c) for c in commands),
            None,
            pat,
        )

    # How it was made. These run on every case.
    existing = {
        r.get("id")
        for v in state.values()
        if isinstance(v, list)
        for r in v
        if isinstance(r, dict)
    }
    missing = [i for i in actions.get("named_ids") or [] if i not in existing]
    add(
        "how.named_ids_exist",
        not missing,
        {"named_but_absent": missing},
        "every id in the final message exists",
    )
    add(
        "how.no_curl_to_api",
        not actions.get("curl_to_api"),
        actions.get("curl_to_api"),
        [],
    )
    skills = actions.get("loaded_skills") or []
    add(
        "how.only_arm_skill_loaded",
        all(s.startswith(f"{arm}:") for s in skills) and bool(skills),
        skills,
        f"{arm}:*",
    )
    return out


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--case", type=Path, required=True)
    ap.add_argument("--state", type=Path, required=True)
    ap.add_argument("--actions", type=Path, required=True)
    ap.add_argument("--arm", required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    res = check(
        yaml.safe_load(a.case.read_text()),
        json.loads(a.state.read_text()),
        json.loads(a.actions.read_text()),
        a.arm,
    )
    a.out.write_text(json.dumps(res, indent=1))
    for r in res:
        print(
            ("PASS " if r["passed"] else "FAIL ")
            + r["check"]
            + (
                ""
                if r["passed"]
                else f"  actual={r['actual']} expected={r['expected']}"
            )
        )


if __name__ == "__main__":
    main()
