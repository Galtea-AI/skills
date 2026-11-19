from pathlib import Path

from script_testing import ScriptTestCase, run_script

SCRIPT = Path(__file__).with_name("check-galtea-changelog.sh")
SKILL_CHANGE = {"skills/galtea/SKILL.md": "skill\nnew\n"}

# (case, files the pull request adds or changes, exit code, text the output holds)
CASES = [
    (
        "a skill change with a valid entry passes",
        {**SKILL_CHANGE, "changelog/20261119-1030-docs-first.md": "major\n\nThe agent reads the docs first.\n"},
        0, "valid major entry",
    ),
    ("a skill change without an entry fails", SKILL_CHANGE, 1, "adds no changelog entry"),
    (
        "two entries fail",
        {**SKILL_CHANGE, "changelog/20261119-1030-a.md": "patch\n\nA.\n", "changelog/20261119-1031-b.md": "patch\n\nB.\n"},
        1, "adds 2 changelog entries",
    ),
    (
        "a badly named entry fails",
        {**SKILL_CHANGE, "changelog/typo-fix.md": "patch\n\nA typo.\n"},
        1, "must be <YYYYMMDD>-<HHMM>-<short-name>.md",
    ),
    (
        "a minor entry fails",
        {**SKILL_CHANGE, "changelog/20261119-1030-step.md": "minor\n\nA new step.\n"},
        1, "must be 'major' or 'patch', got 'minor'",
    ),
    (
        "an entry with no empty second line fails",
        {**SKILL_CHANGE, "changelog/20261119-1030-typo.md": "patch\nA typo.\n"},
        1, "second line must be empty",
    ),
    (
        "an entry with no text fails",
        {**SKILL_CHANGE, "changelog/20261119-1030-empty.md": "patch\n\n\n"},
        1, "says nothing from line 3 on",
    ),
    ("a change outside the skill with no entry passes", {"README.md": "readme\nmore\n"}, 0, "releases nothing"),
    (
        "an entry with no skill change passes",
        {"changelog/20261119-1030-first.md": "major\n\nFirst versioned release.\n"},
        0, "valid major entry",
    ),
]


class CheckGalteaChangelogTest(ScriptTestCase):
    def test_cases(self):
        for case, files, exit_code, needle in CASES:
            with self.subTest(case):
                repo = self.new_repo()
                base = repo.commit({"skills/galtea/SKILL.md": "skill\n", "README.md": "readme\n"})
                head = repo.commit(files)

                result = run_script(SCRIPT, base, head, cwd=repo.path)

                self.assertEqual(result.returncode, exit_code, result.stdout)
                self.assertIn(needle, result.stdout)
