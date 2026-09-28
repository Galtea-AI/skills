"""Run one case against one skill arm, then collect, check and tear down.

An arm is a directory holding a checkout of this repo (use `git worktree add`), loaded
with `--plugin-dir` for this session only. Its directory name becomes the plugin name,
so skills load as `<arm>:galtea`.

The prompt is the user's words, unchanged: no product name, no hints. A case with a
`workspace` starts the agent inside a copy of that fixture project, the way a user
asks from their own repo. The products the run created are found afterwards by
creator and creation time, so do not create products as the same user during a run.

Isolation without `--bare` (which needs ANTHROPIC_API_KEY): the agent runs in a fresh
temp directory, with user settings off, so no CLAUDE.md, memory, hooks or other plugins
enter the context. `check.py` verifies this from the session's init event instead of
assuming it.

The agent's shell runs as you, with full Bash, so `curl` stays observable. Only run
cases you wrote.

Output, per case-run, in runs/<run>/<arm>/<case>/:
  transcript.jsonl  what the agent said and did (stream-json)
  actions.json      calls, failures, claimed ids (from the transcript)
  state.json        what the platform holds, full rows (from the API)
  score.json        level 1 verdict
  teardown.log      what was deleted
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

import actions as actions_mod
import check as check_mod
import collect as collect_mod
import teardown as teardown_mod

HERE = Path(__file__).resolve().parent
SETTLE_S = 900
BUSY = {"PENDING", "AUGMENTING", "EXTENDING"}


def wait_for_datasets(product_ids: list[str]) -> None:
    # The agent may answer before generation ends, as a real one would. Judge the
    # finished datasets, not a snapshot mid-generation.
    if not product_ids:
        return
    deadline = time.time() + SETTLE_S
    while time.time() < deadline:
        rows = (
            collect_mod.galtea(
                "datasets",
                "list",
                "--product-ids",
                ",".join(product_ids),
                "--limit",
                "500",
            )
            or []
        )
        if not any(r.get("status") in BUSY for r in rows):
            return
        time.sleep(20)


EVALS = HERE.parent


def run_case(
    case_path: Path, arm_dir: Path, run_id: str, model: str, keep: bool
) -> Path:
    case = yaml.safe_load(case_path.read_text())
    arm = arm_dir.name
    out = EVALS / "runs" / run_id / arm / case["id"]
    out.mkdir(parents=True, exist_ok=True)

    work = Path(tempfile.mkdtemp(prefix=f"galtea-eval-{case['id']}-"))
    if case.get("workspace"):
        shutil.copytree(
            EVALS / "fixtures" / "workspaces" / case["workspace"],
            work,
            dirs_exist_ok=True,
        )
    for f in case.get("fixtures") or []:
        shutil.copy(EVALS / "fixtures" / f, work / f)
    cmd = [
        "claude",
        "-p",
        case["prompt"],
        "--plugin-dir",
        str(arm_dir),
        "--setting-sources",
        "project",
        "--model",
        model,
        "--output-format",
        "stream-json",
        "--verbose",
        "--max-turns",
        str(case.get("max_turns", 60)),
        "--permission-mode",
        "bypassPermissions",
        "--no-session-persistence",
    ]
    user_id = collect_mod.current_user_id()
    # A minute of slack for clock skew; products that already existed are excluded.
    since = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    before = {p["id"] for p in collect_mod.my_products_since(since, user_id)}
    created: list[dict] = []
    started = time.time()
    try:
        with (out / "transcript.jsonl").open("w") as fh:
            subprocess.run(
                cmd,
                cwd=work,
                stdin=subprocess.DEVNULL,
                stdout=fh,
                stderr=subprocess.STDOUT,
                timeout=case.get("timeout_seconds", 1800),
                check=False,
            )
        acts = actions_mod.parse(out / "transcript.jsonl")
        acts["wall_seconds"] = round(time.time() - started)
        (out / "actions.json").write_text(json.dumps(acts, indent=1))
        created = [
            p
            for p in collect_mod.my_products_since(since, user_id)
            if p["id"] not in before
        ]
        wait_for_datasets([p["id"] for p in created])
        state = collect_mod.collect(created)
        (out / "state.json").write_text(json.dumps(state, indent=1, default=str))
        score = check_mod.check(case, state, acts, arm)
        (out / "score.json").write_text(json.dumps(score, indent=1))
    finally:
        # Teardown runs on failure too, or the next run collects this run's debris.
        if not keep:
            log: list[str] = []
            try:
                if not created:
                    created = [
                        p
                        for p in collect_mod.my_products_since(since, user_id)
                        if p["id"] not in before
                    ]
                for p in created:
                    try:
                        log += teardown_mod.teardown(p["id"])
                    except Exception as e:  # noqa: BLE001 - tear down the other products
                        log.append(f"FAILED product {p['id']}: {e}")
                log = log or ["no product created; nothing to delete"]
            except Exception as e:  # noqa: BLE001 - record and carry on to the next case
                log.append(f"TEARDOWN FAILED: {e}")
            (out / "teardown.log").write_text("\n".join(log) + "\n")
        shutil.rmtree(work, ignore_errors=True)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--arm",
        type=Path,
        action="append",
        required=True,
        help="skill checkout dir; repeat for A/B",
    )
    ap.add_argument("--case", type=Path, action="append", required=True)
    ap.add_argument("--run-id", default=time.strftime("%Y%m%d%H%M"))
    ap.add_argument(
        "--model",
        required=True,
        help="pin it, or an arm change and a model change look the same",
    )
    ap.add_argument(
        "--keep", action="store_true", help="skip teardown, to inspect in the dashboard"
    )
    a = ap.parse_args()
    for arm in a.arm:
        for case in a.case:
            out = run_case(case, arm.resolve(), a.run_id, a.model, a.keep)
            score = (
                json.loads((out / "score.json").read_text())
                if (out / "score.json").exists()
                else []
            )
            failed = [s["check"] for s in score if not s["passed"]]
            print(
                f"{arm.name:12} {case.stem:28} {'PASS' if score and not failed else 'FAIL'}  {', '.join(failed)}"
            )


if __name__ == "__main__":
    main()
