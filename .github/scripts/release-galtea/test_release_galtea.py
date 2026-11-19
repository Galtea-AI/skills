from pathlib import Path

from script_testing import ScriptTestCase, run_script

SCRIPT = Path(__file__).with_name("release-galtea.sh")


def created(tag, sha, title):
    return ["release", "create", tag, "--target", sha, "--title", title, "--notes-file", "release-notes.md"]


class ReleaseGalteaTest(ScriptTestCase):
    def setUp(self):
        self.repo = self.new_repo()
        self.gh = self.new_fake_gh()

    def base(self, *tags):
        """Commits the state before the push, with the given tags on it."""
        before = self.repo.commit({"SKILL.md": "skill\n"})
        for tag in tags:
            self.repo.git("tag", tag)
        return before

    def release(self, sha):
        return run_script(SCRIPT, sha, cwd=self.repo.path, env=self.gh.env)

    def test_the_first_major_entry_releases_1_0_0_with_the_entry_text_as_notes(self):
        self.base()
        sha = self.repo.commit({"changelog/20261119-1030-first.md": "major\n\nThe skill has versions.\nOne release per merge.\n"})

        result = self.release(sha)

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.gh.calls(), [created("galtea--v1.0.0", sha, "galtea 1.0.0")])
        self.assertEqual(self.gh.notes(), "The skill has versions.\nOne release per merge.\n\n")

    def test_the_next_version(self):
        cases = [
            ("a patch entry bumps the last number", ["galtea--v1.2.3"], "patch", "1.2.4"),
            ("a major entry bumps the first number", ["galtea--v1.2.3"], "major", "2.0.0"),
            (
                "the last version is found by version order and other tag series are ignored",
                ["galtea--v1.9.0", "galtea--v1.10.0", "galtea-tracing--v9.0.0"], "patch", "1.10.1",
            ),
        ]
        for case, tags, level, version in cases:
            with self.subTest(case):
                self.repo, self.gh = self.new_repo(), self.new_fake_gh()
                self.base(*tags)
                sha = self.repo.commit({"changelog/20261119-1030-change.md": f"{level}\n\nA change.\n"})

                result = self.release(sha)

                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertEqual(self.gh.calls(), [created(f"galtea--v{version}", sha, f"galtea {version}")])

    def test_a_run_releases_the_entries_of_a_cancelled_run_with_its_own(self):
        self.base("galtea--v1.0.0")
        self.repo.commit({"changelog/20261119-1030-typo.md": "patch\n\nA typo.\n"})
        sha = self.repo.commit({"changelog/20261119-1100-docs.md": "major\n\nReads the docs.\n"})

        result = self.release(sha)

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.gh.calls(), [created("galtea--v2.0.0", sha, "galtea 2.0.0")])
        self.assertEqual(self.gh.notes(), "A typo.\n\nReads the docs.\n\n")

    def test_a_commit_older_than_the_last_release_is_left_alone(self):
        self.base("galtea--v1.0.0")
        older = self.repo.commit({"changelog/20261119-1030-typo.md": "patch\n\nA typo.\n"})
        newer = self.repo.commit({"changelog/20261119-1100-link.md": "patch\n\nA link.\n"})
        self.repo.git("tag", "galtea--v1.0.1", newer)

        result = self.release(older)

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("older than the last release galtea--v1.0.1", result.stdout)
        self.assertEqual(self.gh.calls(), [])

    def test_a_push_with_no_new_entry_releases_nothing(self):
        self.base("galtea--v1.0.0")
        sha = self.repo.commit({"README.md": "readme\n"})

        result = self.release(sha)

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("nothing to release", result.stdout)
        self.assertEqual(self.gh.calls(), [])

    def test_a_commit_that_is_already_released_is_left_alone(self):
        self.base("galtea--v1.0.0")
        sha = self.repo.commit({"changelog/20261119-1030-typo.md": "patch\n\nA typo.\n"})
        self.repo.git("tag", "galtea--v1.0.1", sha)

        result = self.release(sha)

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("already released as galtea--v1.0.1", result.stdout)
        self.assertEqual(self.gh.calls(), [])

    def test_an_entry_with_a_bad_level_is_refused(self):
        self.base("galtea--v1.0.0")
        sha = self.repo.commit({"changelog/20261119-1030-step.md": "minor\n\nA step.\n"})

        result = self.release(sha)

        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("got 'minor'", result.stdout)
        self.assertEqual(self.gh.calls(), [])
