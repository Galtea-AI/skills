# Skill evals

A hand-run harness that checks whether a skill makes an agent use Galtea the way we intend. It runs a real agent on a prompt, reads back what the agent created on the platform, checks it, and deletes it.

Use it to compare a skill change against the current `main`: run the same cases on both, and compare the checks.

## What one case-run does

1. Copies the case's workspace (a small sample project) into a fresh temporary directory, when the case names one.
2. Starts `claude -p` there with only the skill under test loaded (`--plugin-dir`, `--setting-sources project`), and the user's prompt unchanged.
3. Finds the products the current user created during the run, and waits for their datasets to finish generating.
4. Collects every entity under those products from the API (`collect.py`), and parses the transcript (`actions.py`).
5. Checks the case's expectations (`check.py`), plus three checks on every case: each id the agent reported exists, no `curl` to the API, and only the arm's skill loaded.
6. Deletes the products and their metrics (`teardown.py`), also when the run fails.

## Run it

Prerequisites: the `galtea` CLI logged in to the organization to test in, Claude Code logged in, and Python 3.10+ with `pyyaml`.

```bash
# An arm is a checkout of this repo. Compare a branch against main:
git worktree add ../skill-main origin/main

cd evals/harness
python3 run.py --arm ../../../skill-main --arm ../.. \
  --case ../cases/implicit-dataset.yaml --model claude-opus-5-5
python3 report.py ../runs/<run-id>
```

Results land in `runs/<run-id>/<arm>/<case>/`, which git ignores: transcripts and platform state hold organization and product ids.

Each run creates real entities and spends real credits. Run it in a test organization, and do not create products as the same user while it runs: the harness finds the run's products by creator and creation time.

## Cases

A case is a YAML file in `cases/`: an `id`, the `prompt`, an optional `workspace` under `fixtures/workspaces/`, optional `fixtures` files, and `expect`. `check.py` documents the assertion vocabulary.

Write prompts the way a user would: no product name, and no hint about the path the skill should teach.

## Coverage audit

`harness/coverage.py` lists the public CLI commands the skills never name, from the OpenAPI spec the CLI syncs against. It shows where to look first, not what fails: an agent often finds an unnamed command through `--help`.
