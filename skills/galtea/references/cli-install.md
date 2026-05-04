---
name: galtea-cli-install
description: Install paths and verification for the `galtea` CLI on Debian/Ubuntu (apt), Fedora/RHEL/Rocky/Alma (dnf/yum), and any OS with Python 3.9+ (pip). Use when `galtea --version` fails on the user's machine, or before `galtea login` for a first-time setup.
---

# Galtea CLI -- Installation

The `galtea` CLI ships as a single binary, repackaged for the channels users already have. Pick the section that matches the user's OS. Verify with `galtea --version` before continuing to authentication.

The canonical installation page is `https://docs.galtea.ai/cli/installation` -- fetch it (`curl -s https://docs.galtea.ai/cli/installation.md`) when in doubt; that page is updated whenever a new channel ships.

> **Do not run `sudo` install commands on the user's machine without their explicit approval.** Surface the command and the rationale; let the user run it. The `pip` path below avoids `sudo` and is the safest default in agent-driven contexts.

## Python (any OS, no sudo)

If the user has Python 3.9+:

```bash
pip install galtea-cli
```

The `galtea-cli` PyPI package ships per-platform wheels that bundle the same `galtea` binary used by every other channel. The console-script entry point lands in pip's user-scripts directory (typically `~/.local/bin` on Linux/macOS, under `%APPDATA%\Python\` on Windows -- the exact subdirectory is version-suffixed, e.g. `Python313\Scripts`). On most systems that directory is already on `PATH`; if `galtea --version` cannot be found after install, see Troubleshooting below for the canonical lookup. To upgrade later: `pip install --upgrade galtea-cli`.

For a PATH-safe isolated install (recommended when the user already uses pipx or wants the CLI separated from project venvs): `pipx install galtea-cli`.

## Linux (Debian / Ubuntu) -- apt

One-time GPG key + `sources.list` bootstrap, then `apt install`:

```bash
sudo install -m 0755 -d /etc/apt/keyrings

curl -fsSL https://pkgs.galtea.ai/public.key \
  | sudo tee /etc/apt/keyrings/galtea.asc > /dev/null

echo "deb [signed-by=/etc/apt/keyrings/galtea.asc] \
  https://pkgs.galtea.ai/apt stable main" \
  | sudo tee /etc/apt/sources.list.d/galtea.list

sudo apt update
sudo apt install galtea
```

For other channels and the latest instructions, see [pkgs.galtea.ai](https://pkgs.galtea.ai).

## Linux (Fedora / RHEL / Rocky / Alma) -- dnf / yum

One-time GPG key import + repo file, then `dnf install`:

```bash
sudo rpm --import https://pkgs.galtea.ai/public.key

sudo tee /etc/yum.repos.d/galtea.repo <<'EOF'
[galtea]
name=Galtea CLI
baseurl=https://pkgs.galtea.ai/yum
enabled=1
gpgcheck=1
gpgkey=https://pkgs.galtea.ai/public.key
EOF

sudo dnf install galtea
```

Rocky Linux and AlmaLinux use the same repository.

## Verify

```bash
galtea --version
```

A version string (e.g. `galtea version 4.x.y`) confirms the binary is on `PATH`. If the command is not found after a successful install, the install dir is missing from `PATH`:

- `pip` / `pipx` install: ensure the user-bin dir (`python3 -m site --user-base`/`bin`, or `~/.local/bin`) is on `PATH`.
- apt / dnf install: the binary lands at `/usr/bin/galtea` -- a missing `/usr/bin` from `PATH` is unusual, suspect a custom shell rc.

## Troubleshooting

- **`galtea: command not found`** after install -- see PATH note above.
- **GPG key import failed** on apt -- confirm the user has `curl` and that `https://pkgs.galtea.ai` is reachable from their network.
- **`No module named pip`** -- install pip first. On most systems: `python3 -m ensurepip --upgrade`. Debian/Ubuntu often ship Python with `ensurepip` disabled, in which case use the system package manager: `sudo apt install python3-pip`. Fedora/RHEL: `sudo dnf install python3-pip`.

For anything not covered above, point the user at `support@galtea.ai` (product issue) or open feedback on this skill via [skill-feedback.md](skill-feedback.md) (skill issue).
