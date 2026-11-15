"""Per case, per arm: level 1 result, turns, galtea calls, fabricated ids, cost.
Failed checks are printed underneath. No aggregate number, on purpose."""

from __future__ import annotations

import json
import sys
from pathlib import Path

run = Path(sys.argv[1])
rows, fails = [], []
for score_f in sorted(run.glob("*/*/score.json")):
    arm, case = score_f.parent.parent.name, score_f.parent.name
    score = json.loads(score_f.read_text())
    acts = json.loads((score_f.parent / "actions.json").read_text())
    bad = [s for s in score if not s["passed"]]
    absent = next(
        (
            s["actual"]["named_but_absent"]
            for s in score
            if s["check"] == "how.named_ids_exist"
        ),
        [],
    )
    rows.append(
        f"| {case} | {arm} | {'PASS' if not bad else 'FAIL'} | {acts.get('turns')} | {len(acts.get('galtea_calls') or [])} | {len(absent)} | {round(acts.get('cost_usd') or 0, 2)} |"
    )
    fails += [
        f"- {case} / {arm}: `{s['check']}` actual={s['actual']} expected={s['expected']}"
        for s in bad
    ]
print(
    "| case | arm | level 1 | turns | galtea calls | fabricated ids | cost (USD) |\n|---|---|---|---|---|---|---|"
)
print("\n".join(rows))
if fails:
    print("\nFailed checks:\n" + "\n".join(fails))
