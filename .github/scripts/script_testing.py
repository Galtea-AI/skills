"""Helpers for the .github/scripts tests: a throwaway git repository and a fake gh.

Standard library only, Python 3.9+, on Linux, macOS and Windows (with Git for Windows).
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

# Logs each call as one line of arguments ended by \x1f, and keeps the notes file it was given.
FAKE_GH = """#!/usr/bin/env bash
printf '%s\\037' "$@" >> "$FAKE_GH_DIR/calls.log"
printf '\\n' >> "$FAKE_GH_DIR/calls.log"
while [ $# -gt 0 ]; do
  if [ "$1" = "--notes-file" ]; then cp "$2" "$FAKE_GH_DIR/notes.md"; fi
  shift
done
"""


def _bash() -> str:
    """Git Bash on Windows, where `bash` on the PATH may be WSL's or missing; `bash` elsewhere."""
    if os.name != "nt":
        return "bash"
    git = shutil.which("git")
    for root in Path(git).resolve().parents if git else []:
        for candidate in (root / "bin" / "bash.exe", root / "usr" / "bin" / "bash.exe"):
            if candidate.exists():
                return str(candidate)
    return "bash"


def run_script(script: Path, *args: str, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Runs a script outside of Actions, with stdout and stderr merged."""
    full_env = {key: value for key, value in os.environ.items() if key != "GITHUB_STEP_SUMMARY"}
    full_env.update(env or {})
    return subprocess.run(
        [_bash(), script.as_posix(), *args],
        cwd=cwd,
        env=full_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
    )


class Repo:
    def __init__(self, path: Path):
        self.path = path

    def git(self, *args: str) -> str:
        done = subprocess.run(
            ["git", *args], cwd=self.path, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True
        )
        return done.stdout.strip()

    def commit(self, files: dict[str, str] | None = None) -> str:
        """Writes the files with LF line ends, commits everything and returns the new commit."""
        for name, text in (files or {}).items():
            file = self.path / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(text.encode())
        self.git("add", "--all")
        self.git("commit", "--quiet", "--message", "commit")
        return self.git("rev-parse", "HEAD")


class FakeGh:
    def __init__(self, path: Path):
        self.path = path
        bin_dir = path / "bin"
        bin_dir.mkdir()
        (bin_dir / "gh").write_bytes(FAKE_GH.encode())
        (bin_dir / "gh").chmod(0o755)
        self.env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "FAKE_GH_DIR": path.as_posix()}

    def calls(self) -> list[list[str]]:
        log = self.path / "calls.log"
        return [line.split("\x1f")[:-1] for line in log.read_text().splitlines()] if log.exists() else []

    def notes(self) -> str:
        return (self.path / "notes.md").read_text()


def _remove_read_only(func, path, _exc_info):
    os.chmod(path, stat.S_IWRITE)
    func(path)


class ScriptTestCase(unittest.TestCase):
    def tmp_dir(self) -> Path:
        path = Path(tempfile.mkdtemp())
        # Git's object files are read-only, which Windows refuses to delete.
        self.addCleanup(shutil.rmtree, path, onerror=_remove_read_only)
        return path

    def new_repo(self) -> Repo:
        repo = Repo(self.tmp_dir())
        repo.git("init", "--quiet", "--initial-branch", "main")
        config = {"user.email": "t@t", "user.name": "t", "commit.gpgsign": "false", "tag.gpgsign": "false", "core.autocrlf": "false"}
        for key, value in config.items():
            repo.git("config", key, value)
        return repo

    def new_fake_gh(self) -> FakeGh:
        return FakeGh(self.tmp_dir())
