---
name: unmute
description: Send this repository's Claude Code sessions to Galtea again
disable-model-invocation: true
allowed-tools: Bash(sh *scripts/run.sh" unmute *)
---
!`sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" unmute "${CLAUDE_PLUGIN_DATA}"`

Repeat the line above to the user, word for word. Do nothing else.
