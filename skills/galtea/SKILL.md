---
name: galtea
description: Interact with the Galtea platform -- the AI product testing and evaluation platform for AI/LLM products -- and access its documentation. Use when needing to (1) query or modify Galtea data (products, specifications, datasets, metrics including decision (formerly classifier) metrics, evaluations, runs, monitors, etc.) via the `galtea` CLI, (2) set up a test pipeline for an AI product, (3) run or monitor evaluations of an AI product, or set up production monitors and alerts, (4) wire an AI product into Galtea via the Python SDK, (5) upload a dataset the user already has, including a CSV with documents, images, or audio attached to its rows, and test a product that reads them, (6) run the whole improvement loop for an AI product: specifications, datasets, evaluations, then changes based on what failed, or (7) look up Galtea docs, concepts, or CLI/SDK usage.
---

# Galtea

Galtea is an AI product testing and evaluation platform. Teams describe what their AI product should do in specifications, generate datasets of test cases from them, evaluate the product, and improve it based on what failed.

This skill drives the **`galtea` CLI** for the user. It holds only how to work: install, log in, read the docs, and act. Every fact about the platform -- commands, fields, limits, defaults, errors, and what each release changed -- comes from the docs of the deployment the user is logged in to and from `galtea <noun> <verb> --help`, never from this file or from memory.

## 1. Install and log in

1. Run `galtea --version`. If the command is not found, install the CLI: [references/cli-install.md](references/cli-install.md). Do not run `sudo` install commands without the user's approval; show the command and let them run it.
2. Run `galtea whoami`. If it is not logged in, ask the user to run `galtea login` themselves and paste their API key at its prompt. For a self-hosted or dedicated platform, they add `--host <their platform address>`. A user without a key creates one in the Galtea dashboard settings.
3. **If any `galtea` command exits with code `10`, the CLI version does not match the platform.** It prints the install command for a version that does: run it, or show it to the user, then retry. Do not work around it.
4. **If `galtea docs` does not exist, the CLI is too old for this skill.** Install the latest CLI first.

### The API key

- Never ask for the key through a structured-question tool (option pickers and forms): their contents end up in logs. Take it as free text in the chat, or let the user paste it at the `galtea login` prompt.
- Never echo the key back, write it to a tracked file, or pass it as a command-line argument. For a non-interactive run, set it in the environment variable that `galtea login --help` names.
- Never run a CLI command in a debug or verbose mode that prints request headers: the output contains the key. Give that command to the user to run in their own terminal.

## 2. Read the docs first

Galtea ships often, and each deployment can run a different release. Before you advise on a workflow, a concept or an argument, read it in the docs of the user's deployment.

1. Run `galtea docs`. It prints the platform release the user is logged in to, the address of its docs (`Docs:`), and the index of the docs pages (`Index:`).
2. Fetch the index. When it lists releases, open the one for the major and minor of the `Platform:` release (5.19.0 is 5.19), and find the pages for the task there. When the index does not list that release yet, its docs are not published: use the newest release it lists, and tell the user once. Search it by topic: a concept, a tutorial, a CLI command, an API operation.
3. Open each page as Markdown, at the `.md` address its link gives. When a link has no `.md`, put its path under the `Docs:` address and add `.md`. Prefer this over the HTML page: it has the full text. Once the index was read, a page that answers 404 was added after the release you opened, so that release does not have the feature.
4. **Read the known issues of that release before you build anything, and again whenever a call does not behave as its docs say.** They are at `agents/known-issues.md` under the docs address of the release you opened in step 2: `<Docs>/agents/known-issues.md` for the user's release, the older release's address when its docs are not published yet. The page lists calls that behave differently from the API reference in that release, and what works instead. A 404 means that release lists none.
5. For the exact shape of a command, run `galtea <noun> <verb> --help`. `galtea --help` lists the nouns, and `galtea <noun> --help` lists the verbs.

**When the deployment's docs cannot be read**, use the public docs at `https://docs.galtea.ai` the same way, starting from `https://docs.galtea.ai/llms.txt`. This applies when `galtea docs` says on stderr that it printed the public docs, or when the `Index:` does not return an index, whatever the error, a 404 included: some docs hosts serve no `llms.txt` and no `.md` pages. Open the user's release there as step 2 says. When you read another release, because `Platform:` is unknown or the index does not list it, tell the user once that those docs may not match their platform.

**When the docs and the platform disagree, the platform wins.** Trust the live response and `--help`, finish the task, and tell the user what differed (see "Skill feedback").

If your agent has a web-fetch tool, use it to read pages. Use `curl` when you need the raw text in a shell pipeline.

## 3. How to work

- **Use the CLI for everything that talks to the platform.** Never hand-craft HTTP requests when a `galtea` command exists. Use the Python SDK when the work runs the user's AI product from Python, or when the docs say the CLI cannot do a step. The docs page of each task says which tool does what.
- **Start from what the user has.** Read their code, prompts and documents before you propose anything. Draft the product details and specifications from them, show the list, and create them only after the user agrees. Never invent behaviours the user did not describe: ask.
- **Let the platform generate test content.** Galtea generates datasets of test cases from specifications. Prefer that over writing test cases by hand, also when the user does not name the feature. When the user already has test content, upload it instead of generating new content. A handful of hand-written rows in a dataset is fine.
- **Keep each product change behind its own version**, so results stay comparable between iterations. Register a new version before you evaluate a changed product.
- **Platform work is asynchronous.** Generation and evaluation finish in the background. Poll their status in one list call per cycle, not one call per item, and stop when nothing is left pending.
- **Report results by the verdict the platform gives.** Each evaluation says whether it passed its metric's threshold: use that, never a cut-off of your own. An evaluation that was skipped or failed to run usually points to a setup problem, not to a weakness of the product: find the reason before you call it a product failure.
- **Not every failure is the product's fault.** When the product's answer is acceptable, the specification may be wrong or vague. When the judge's reason contradicts the answer, the metric may be wrong. Propose the matching change, and never change a metric just to make failures disappear.
- **Some operations spend the organization's credits**, such as generation and evaluation. Before a large run, say what it will do and check the credit status as the docs describe.
- **Read every error body.** It says what went wrong more precisely than the status code. Never retry a refused write blindly.
- **Pick the smallest set of calls that answers the user.** Show what you created, with its id and where to see it in the dashboard.

## 4. The improvement loop

When the user wants to improve an AI product with Galtea end to end, run the loop that the docs playbook for improving a product with an AI agent describes. Find it in the index. In short:

1. Define specifications with the user.
2. Generate datasets from them, and the metrics that score them.
3. Evaluate a version of the product.
4. Group the failures, propose product, specification or metric changes, and let the user decide.
5. Register a new version for the change and evaluate again. Report what improved and what regressed.

Repeat until the failures that matter are gone.

## Skill feedback

When the user says this skill gave wrong or outdated guidance, a workflow did not give the expected result, or the skill misses something, offer to send feedback to the skill maintainers. Follow [references/skill-feedback.md](references/skill-feedback.md). This is for the skill only: send problems with the Galtea product itself to `support@galtea.ai`.

## When not to use this skill

- **Building the AI product itself.** This skill evaluates products; it does not write them.
- **Browsing results.** When the user only wants to look at results, point them to the Galtea dashboard.
