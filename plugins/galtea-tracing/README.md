# galtea-tracing

A Claude Code plugin that sends each conversation to [Galtea](https://galtea.ai) as a production session. Each turn is a trace, with its model calls, tool calls and subagents as spans. Galtea monitors can then evaluate your coding sessions like any other production traffic.

## Install

Add the Galtea marketplace, then install the plugin:

```
/plugin marketplace add Galtea-AI/skills
/plugin install galtea-tracing@galtea
```

The install dialog asks for four values:

| Option | What to enter |
|---|---|
| Galtea API key | Your `gsk_` key from the Galtea dashboard, **Settings → API Keys**. An older `gsk-` key also works. Claude Code keeps it in its secure storage. |
| Galtea product id | The product that receives your sessions. Copy the id from the product page. |
| Galtea API URL | Leave `https://api.galtea.ai`, unless you use a self-hosted Galtea. It must start with `https://`; `http://` is accepted only for `localhost`. |
| Redact secrets | On by default. See [Redaction](#redaction). |

To change a value later, open `/plugin`, pick `galtea-tracing`, and edit its options. After a product change, a resumed conversation continues in a session of the new product: the plugin finds it by its `customId`, or creates it.

The plugin needs Python 3.9 or later. It uses only the standard library.

## Updates and self-hosted Galtea

The plugin has no fixed version: each change merged to this repository is a new version. Claude Code does not update plugins from this marketplace on its own. Run `/plugin marketplace update galtea` to get the latest one, or turn on auto-update for the marketplace in `/plugin`.

A self-hosted Galtea that follows its own release cycle can stay on one release of the plugin. Add the marketplace at a release tag, named `galtea-tracing--v<version>` on the repository's Tags page, instead of the main branch:

```
/plugin marketplace add Galtea-AI/skills#galtea-tracing--v<version>
```

This holds the whole marketplace, the `galtea` skill included, at that tag.

## The setup check

The first session after you install, or after you change the key, product or URL, runs a check. It shows one message with the result. The check does four things:

- It reads the product, to confirm the key and the product id.
- It reads the product's auto-close setting and its policy for turns on a closed session.
- It reads `showThinkingSummaries` in your Claude Code settings: `settings.json` in `~/.claude` (or in `$CLAUDE_CONFIG_DIR`), and the project's `.claude/settings.json` and `.claude/settings.local.json`.
- It runs a self-test. The self-test sends one synthetic turn to a **development** session, reads it back, and deletes it. It never closes that session, so no monitor evaluates it. If the delete fails, the check fails and the message names the session to delete by hand.

An API key that does not start with `gsk_` or `gsk-`, or that holds a space or a line break, is never sent: the session start message says the key is not valid, and nothing is logged to Galtea until you fix it. The same holds for a product id with a space and an API URL that is not `https://`.

When the check fails, it runs again when you next start or resume a session. It also runs again after Galtea refuses the key (HTTP 401 or 403) on any call, for example when the key was revoked. It does not run on `/clear`, a compaction or a fork. The message names one of four failures:

- **Refused:** Galtea answered 401, 403 or 404 when the check read the product.
- **Error:** Galtea answered with another error status. A redirect counts as an error: the plugin never follows one, because it would carry your API key to another host.
- **Could not reach:** Galtea did not answer the product read or a self-test step. The check then waits 10 minutes before it runs again, so a host that does not answer stalls one session start, not each one.
- **Self-test failed:** a step after the product read failed.

## What is sent

One Claude Code session becomes one Galtea session. Its `customId` is the Claude Code session id. The plugin creates the Galtea session when it sends the first turn, so a conversation with no answered turn leaves nothing in Galtea.

- **Version:** the session goes to the product version whose only fact is `model`: the model of the conversation's first answer, such as `claude-opus-5-5`. The plugin creates that version on first use. Galtea links it to the model of the same name, so each turn gets a cost from the model's prices. The cost counts every model call of the turn at that model's prices, subagents included, and leaves out cache writes. A session stays on its version after `/model`. A model name Galtea does not know, which can happen with Amazon Bedrock or Google Vertex AI, gets a version with no price, so its turns show no cost. When Galtea refuses the version (for example, a self-hosted Galtea without `POST /versions/get-or-create`, or a key that cannot create versions), the session goes to the product's latest version and `hook.log` names the error.
- **Session metadata:** the working directory, your user name, your git email (`git config user.email`), the plugin version, and the git remote, branch and commit. The git remote, branch and commit are left out outside a repository. A remote URL never carries its user name or password, also in the `user@host:path` form.
- **Session title:** the session name, in metadata as `title`. `title_source` is `custom` for a name you set with `/rename`, which wins, or `ai` for the name Claude Code generated. It is sent after a turn or at session end, only when it changed, and redacted like other text.
- **Each turn (a trace):** your prompt, Claude's final reply, the latency from your prompt to the reply, and the input, output and cache-read token counts. The prompt and the reply are each cut at 100 KB as sent, escapes included, so a turn fits the API's 250 KB request limit.
- **Spans of each turn:** each model call with its thinking summary, text and tool names; each tool call with its input and output; each subagent with its own steps nested below it. A span field is cut at 20 KB, and the error of a failed tool call at 2 KB.
- When another plugin's Stop hook makes Claude continue a turn, the turn gets the final reply and the spans of the continued work. A tool call that was still running at the first reply is sent once it ends, with its output. A span is never sent twice.
- `claude -p` runs and one-shot Agent SDK `query()` calls are not logged. Claude Code stops their hooks before the turn can be sent. A long-lived Agent SDK session (`ClaudeSDKClient` in Python, a streaming prompt in TypeScript) loads the plugin like Claude Code and is logged like an interactive session.

The plugin reads the session transcript file to build the spans. Anthropic documents that format as internal. If it changes, the turn is still sent and only the spans and the title are lost.

## Redaction

Redaction is on by default. It is best-effort: it can miss a secret, so do not rely on it alone.

It masks these values with `[REDACTED]` in prompts, replies, and every span input, output, error and thinking text:

- API keys with a known prefix: Galtea (`gsk_` and `gsk-`), Anthropic, OpenAI, AWS access key ids, GitHub, Slack, Google, Stripe (`sk_live_`, `sk_test_`, `rk_live_`, `rk_test_`), npm, Hugging Face and GitLab.
- JSON Web Tokens and PEM private key blocks.
- The value of `Authorization:` and `x-api-key:` headers, and of a bare `Bearer` token.
- The password in a URL such as `postgres://user:password@host`.
- The value of a secret name in `NAME=value`, in a JSON or Python dict field, or in a line that is only `name: value`, as in YAML. A secret name contains `SECRET`, `TOKEN`, `PASSWORD`, `PASSWD`, `PASSPHRASE`, `CREDENTIAL`, `API_KEY`, `APIKEY`, `PRIVATE_KEY` or `ACCESS_KEY`, or has `KEY`, `PASS` or `PWD` after another part of the name, as in `DB_PASS`, `api-key` or `AccountKey` in a connection string. A bare `key` or `PWD` is not a secret name. The keyword must end a word or a camelCase part, so `clientSecret` counts and `tokenizer` does not. The name stays, and a quoted value is masked up to its closing quote.
- Kept values: digits only, such as `max_tokens=100000`, and code after `=` that calls a function or reads a variable of the same name, such as `password=get_password(user)` or `api_key=settings.API_KEY`. The `name: value` rule also masks a type in code, such as `password: string`.

If redaction fails on a field, that field is sent as `[REDACTION FAILED]`, never as the raw text. Turn redaction off in the plugin options only if you are sure your sessions hold no secrets.

## Stop logging

- `/galtea-tracing:mute` stops logging for the current repository, also when you run it in a subfolder. Run it again in another repository to mute that one too. Outside a repository, it mutes the current folder and every folder below it.
- `/galtea-tracing:unmute` starts logging the current repository again. When a parent folder is muted, the repository stays muted, and the message names the folder to unmute.
- `GALTEA_CC_DISABLE=1 claude` skips logging for that one Claude Code process.

Only you can run the two commands: Claude does not run them on its own. If the mute list cannot be read, every repository counts as muted until the file is fixed or deleted.

## Thinking summaries

Claude Code writes thinking text to the transcript only when `showThinkingSummaries` is on. A plugin cannot change that setting, so the setup check tells you when it is off. To log thinking, add this to `~/.claude/settings.json` (or `settings.json` in `$CLAUDE_CONFIG_DIR`):

```json
{ "showThinkingSummaries": true }
```

## When a session closes

`/exit` closes the Galtea session, and Galtea monitors then evaluate it. If you close the terminal instead, the session can stay open. Galtea's auto-close then closes it after the product's inactivity time, 30 minutes by default. Auto-close also closes the session when you exit while the plugin is still sending the last turn: the plugin leaves the session open so that monitors evaluate it with all its spans. The setup check warns you when the product does not auto-close production sessions.

`claude --resume` on a closed session reopens it in Galtea, and monitors evaluate it again. This needs the product's closed-session policy to be `REOPEN_AND_REEVALUATE`, the default. With `IGNORE` or `REJECT`, Galtea drops or refuses the new turns, and the setup check warns about it. Resume also works after the plugin's local state for the session is gone: the plugin finds the Galtea session by its `customId` and adds the new turns after the last one. If that lookup fails, the turn is not sent, rather than starting a second session for the conversation; the next turn looks again.

If you delete the session in Galtea, the next turn of the conversation creates it again, starting from that turn.

## Windows

Claude Code on Windows runs hooks through Git Bash. There, `python3` is often missing. The plugin tries `python3`, then `python`, then the `py -3` launcher, and uses the first one that is Python 3.9 or later. With no Python, the plugin sends nothing and Claude Code works as usual. Each session start then shows one message that says Galtea tracing needs Python 3.9 or later.

## Logs and troubleshooting

An error in the plugin never makes Claude Code fail. The setup check can delay one session start by up to about 25 seconds when Galtea does not answer. The plugin writes each error to `hook.log` in its data directory:

```
~/.claude/plugins/data/galtea-tracing-galtea/hook.log
```

With `CLAUDE_CONFIG_DIR` set, the folder is under it instead of `~/.claude`. On Linux and macOS, only your user can read the files the plugin creates there.

When a turn is sent without its spans, because the transcript or a subagent transcript is missing or holds no line for the turn, `hook.log` says so in one line. The log never holds transcript content. Past 1 MB, it moves to `hook.log.1`, so the two files hold at most about 2 MB.

The same directory holds the mute list and a small state file per session. State files older than 7 days are deleted.

To report a problem with the plugin, [open an issue](https://github.com/Galtea-AI/skills/issues/new?template=skill-feedback.yml) and attach the matching `hook.log` lines.

## Uninstall

```
/plugin uninstall galtea-tracing@galtea
```

Claude Code deletes the plugin's data directory. Sessions already sent stay in Galtea.

## Development

Run the tests from the repository root. They make no network calls. CI runs them on Linux, macOS and Windows for every change to this folder.

```bash
python3 -m unittest discover plugins/galtea-tracing/tests -v
```

Load a local copy with `claude --plugin-dir plugins/galtea-tracing`. Set `GALTEA_CC_API_URL` to send to another Galtea host. It overrides the API URL option.

To release a version that self-hosted users can pin, tag the merge commit `galtea-tracing--v<version>` and push the tag.
