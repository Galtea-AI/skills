---
name: galtea-skill-feedback
description: Submit feedback about the Galtea skill to its maintainers via a GitHub Issue on Galtea-AI/skills. Use when the user indicates the skill gave incorrect guidance, is missing information, or could be improved.
---

# Skill Feedback

Follow these steps exactly.

## 1. Ask permission

Ask the user whether they'd like you to submit feedback to the skill maintainers. Make it clear this is about *the skill* (the agent instructions you are reading), not about Galtea the product — product issues should go to `support@galtea.ai`. If they decline, move on.

## 2. Draft the feedback

Write the feedback using the form below, present it to the user, and ask whether they'd like to change anything before submitting.

**Describe the issue** (required)
A clear description of what went wrong or what could be improved. Include:
- What the user was trying to do
- What the skill did vs. what was expected
- Any specific instructions that were incorrect, outdated, or missing

**What would the ideal outcome look like?** (optional)
What the correct behavior or guidance should be.

Format the body as markdown using the two labels above as `##` headings.

## 3. Submit

Create a GitHub Issue on the `Galtea-AI/skills` repository. Per the Galtea monorepo convention (see `CLAUDE.md`), every AI-created issue gets the `waiting-for-human-check` label and is **left unassigned** so the team can triage it.

```bash
gh api repos/Galtea-AI/skills/issues \
  -X POST \
  -f title="<concise title, e.g.: skill-feedback: auth flow 401s when key file missing>" \
  -f body="$(cat <<'EOF'
## Describe the issue
<filled-in body>

## What would the ideal outcome look like?
<filled-in body, or omit the section if blank>
EOF
)" \
  -f "labels[]=skill-feedback" \
  -f "labels[]=waiting-for-human-check" \
  --jq '.html_url'
```

The command prints the new issue URL on success — share it with the user.

### Fallback

If `gh` is not authenticated or the request fails, give the user this link to file the issue manually:

```
https://github.com/Galtea-AI/skills/issues/new?labels=skill-feedback,waiting-for-human-check&title=skill-feedback%3A%20&body=%23%23%20Describe%20the%20issue%0A%0A%23%23%20What%20would%20the%20ideal%20outcome%20look%20like%3F%0A
```

## Don't

- Don't submit product bug reports via this flow — those go to `support@galtea.ai`.
- Don't self-assign or assign anyone else; leave the issue unassigned for triage.
- Don't strip the `waiting-for-human-check` label — the team relies on it to identify AI-created issues.
