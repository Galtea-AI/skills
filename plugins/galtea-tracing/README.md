# galtea-tracing

A Claude Code plugin that sends each conversation to [Galtea](https://galtea.ai) as a production session. Each turn carries its model calls, tool calls and subagents as traces. Galtea monitors can then evaluate your coding sessions like any other production traffic.

## Install

Add the Galtea marketplace, then install the plugin:

```
/plugin marketplace add Galtea-AI/skills
/plugin install galtea-tracing@galtea
```

The install dialog asks for four values:

| Option | What to enter |
|---|---|
| Galtea API key | Your `gsk_` key from the Galtea dashboard, **Settings → API Keys**. Claude Code keeps it in its secure storage. |
| Galtea product id | The product that receives your sessions. Copy the id from the product page. |
| Galtea API URL | Leave `https://api.galtea.ai`, unless you use a self-hosted Galtea. |
| Redact secrets | On by default. See [Redaction](#redaction). |

To change a value later, open `/plugin`, pick `galtea-tracing`, and edit its options.

The plugin needs Python 3.9 or later. It uses only the standard library.

## The setup check

The first session after you install, or after you change the key, product or URL, runs a check. It shows one message with the result. The check does four things:

- It reads the product, to confirm the key and the product id.
- It reads the product's auto-close setting.
- It reads `showThinkingSummaries` in `~/.claude/settings.json`.
- It runs a self-test. The self-test sends one synthetic turn to a **development** session, reads it back, and deletes it. It never closes that session, so no monitor evaluates it.

When the check fails, it runs again when you next start or resume a session. It does not run on `/clear` or a compaction, so a Galtea host that does not answer never stalls those. It says the key or product id was refused only when Galtea answers 401, 403 or 404. Any other failure is reported as Galtea being unreachable.

## What is sent

One Claude Code session becomes one Galtea session. Its `customId` is the Claude Code session id.

- **Session metadata:** the working directory, your user name, the plugin version, and the git remote, branch and commit. The git values are left out outside a repository. A remote URL never carries its user name or password.
- **Session title:** the session name, in metadata as `title`. `title_source` is `custom` for a name you set with `/rename`, which wins, or `ai` for the name Claude Code generated. It is sent after a turn or at session end, only when it changed, and redacted like other text.
- **Each turn:** your prompt, Claude's final reply, the latency, and the input, output and cache-read token counts.
- **Traces of each turn:** each model call with its thinking summary, text and tool names; each tool call with its input and output; each subagent with its own steps nested below it. A trace field is cut at 20 KB.
- `claude -p` runs are not logged. Claude Code stops their hooks before the turn can be sent.

The plugin reads the session transcript file to build the traces. Anthropic documents that format as internal. If it changes, the turn is still sent and only the traces and the title are lost.

## Redaction

Redaction is on by default. It is best-effort: it can miss a secret, so do not rely on it alone.

It masks these values with `[REDACTED]` in prompts, replies, and every trace input, output, error and thinking text:

- API keys with a known prefix: Galtea (`gsk_`), Anthropic, OpenAI, AWS access key ids, GitHub, Slack and Google.
- JSON Web Tokens and PEM private key blocks.
- The value of `Authorization:` and `x-api-key:` headers, and of a bare `Bearer` token.
- The password in a URL such as `postgres://user:password@host`.
- The value of a variable or JSON field whose name contains `SECRET`, `TOKEN`, `PASSWORD`, `PASSWD`, `API_KEY`, `APIKEY`, `PRIVATE_KEY` or `ACCESS_KEY`. The keyword must end a word or a camelCase part, so `clientSecret` counts and `tokenizer` does not. The name stays. Two values are kept: digits only, such as `max_tokens=100000`, and code that names another such variable, such as `password=password` or `max_tokens=max_tokens)`.

If redaction fails on a field, that field is sent as `[REDACTION FAILED]`, never as the raw text. Turn redaction off in the plugin options only if you are sure your sessions hold no secrets.

## Stop logging

- `/galtea-tracing:mute` stops logging for the current repository. Run it again in another repository to mute that one too.
- `/galtea-tracing:unmute` starts logging the current repository again.
- `GALTEA_CC_DISABLE=1 claude` skips logging for that one Claude Code process.

## Thinking summaries

Claude Code writes thinking text to the transcript only when `showThinkingSummaries` is on. A plugin cannot change that setting, so the setup check tells you when it is off. To log thinking, add this to `~/.claude/settings.json`:

```json
{ "showThinkingSummaries": true }
```

## When a session closes

`/exit` closes the Galtea session, and Galtea monitors then evaluate it. If you close the terminal instead, the session can stay open. Galtea's auto-close then closes it after the product's inactivity time, 30 minutes by default. The setup check warns you when the product does not auto-close production sessions.

`claude --resume` on a closed session reopens it in Galtea, and monitors evaluate it again. This also works after the plugin's local state for the session is gone: the plugin finds the Galtea session by its `customId` and adds the new turns after the last one.

## Windows

Claude Code on Windows runs hooks through Git Bash. There, `python3` is often missing. The plugin tries `python3`, then `python`, then the `py -3` launcher, and uses the first one that is Python 3.9 or later. With no Python, the plugin does nothing and Claude Code works as usual.

## Logs and troubleshooting

The plugin never stops or slows Claude Code because of an error. It writes each error to `hook.log` in its data directory:

```
~/.claude/plugins/data/galtea-tracing-galtea/hook.log
```

The same directory holds the mute list and a small state file per session. State files older than 7 days are deleted.

## Uninstall

```
/plugin uninstall galtea-tracing@galtea
```

Claude Code deletes the plugin's data directory. Sessions already sent stay in Galtea.

## Development

Run the tests from the repository root. They make no network calls.

```bash
python3 -m unittest discover plugins/galtea-tracing/tests -v
```

Load a local copy with `claude --plugin-dir plugins/galtea-tracing`. Set `GALTEA_API_URL` to send to another Galtea host. It overrides the API URL option.
