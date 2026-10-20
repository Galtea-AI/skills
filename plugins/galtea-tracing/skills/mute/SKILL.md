---
name: mute
description: Stop sending this repository's Claude Code sessions to Galtea
disable-model-invocation: true
allowed-tools: Bash(sh *scripts/run.sh" mute *)
---
!`sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" mute "${CLAUDE_PLUGIN_DATA}"`

Repeat the line above to the user, word for word. Do nothing else.
