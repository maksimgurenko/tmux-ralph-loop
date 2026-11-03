# Install Ralph

Use this guide when asked to install or update Ralph on Linux or macOS. Install
the single release executable; a source checkout is unnecessary.

## 1. Choose the installation scope

Ask the user: **“Install globally for your user, or only in this project?”**
An explicit scope in the original request already answers this question.

| Scope | Destination | Invocation |
| --- | --- | --- |
| Global for this user | `~/.local/bin/ralph` | `ralph` through `PATH` |
| This project | `<project>/ralph` | `./ralph` from the project |

Resolve the target project before a project installation. Use the selected scope
for updates too. Finish installation without starting a loop.

## 2. Check prerequisites

Require **Python 3.9+**, **tmux**, **Git**, **curl**, and the chosen agent CLI.
Check `python3 --version`, `tmux -V`, and `git --version`. Install missing system
dependencies with the host's package manager:

| System | Command |
| --- | --- |
| macOS (Homebrew) | `brew install python tmux git curl` |
| Debian / Ubuntu | `sudo apt update && sudo apt install python3 tmux git curl` |
| Fedora | `sudo dnf install python3 tmux git curl` |
| Arch Linux | `sudo pacman -S --needed python tmux git curl` |

If the distribution supplies an older Python, arrange a Python 3.9+ installation
and confirm `python3` resolves to it before proceeding. No Python packages are needed.
Only the selected agent CLI is required; use its official installation and login
instructions linked in [agent setup](docs/agents.md). GitHub issue workflows also
need authenticated `gh`; GitLab issue workflows need authenticated `glab`.

## 3. Download, verify, and install

Default to the latest stable GitHub release. If the user requests a specific
release, set `RALPH_VERSION` to its `vX.Y.Z` tag. Resolve the tag once so both
downloads come from the same release.

Set `RALPH_DEST` to the chosen destination before running the Bash block below:

```sh
# Global scope:
export RALPH_DEST="$HOME/.local/bin/ralph"
# Project scope (use the actual absolute project path instead):
# export RALPH_DEST="/absolute/path/to/project/ralph"
```

```bash
set -euo pipefail
: "${RALPH_DEST:?Set RALPH_DEST to the selected installation destination}"
release_repo="https://github.com/maksimgurenko/tmux-ralph-loop"
release_tag="${RALPH_VERSION:-$(curl -fsSL "https://api.github.com/repos/maksimgurenko/tmux-ralph-loop/releases/latest" |
  python3 -c 'import json, sys; print(json.load(sys.stdin)["tag_name"])')}"
if [[ ! "$release_tag" =~ ^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]; then
  echo "Expected a stable release tag vX.Y.Z, got: $release_tag" >&2
  exit 1
fi
download_dir="$(mktemp -d)"
trap 'rm -rf "$download_dir"' EXIT
curl -fsSL "$release_repo/releases/download/$release_tag/ralph" -o "$download_dir/ralph"
curl -fsSL "$release_repo/releases/download/$release_tag/SHA256SUMS" -o "$download_dir/SHA256SUMS"
python3 - "$download_dir" <<'PY'
import hashlib
from pathlib import Path
import re
import sys

directory = Path(sys.argv[1])
checksum = (directory / "SHA256SUMS").read_text(encoding="ascii")
match = re.fullmatch(r"([0-9a-f]{64})  ralph\n?", checksum)
actual = hashlib.sha256((directory / "ralph").read_bytes()).hexdigest()
if match is None or actual != match[1]:
    sys.exit("Ralph checksum verification failed; installation stopped")
PY
mkdir -p "$(dirname "$RALPH_DEST")"
install -m 755 "$download_dir/ralph" "$RALPH_DEST"
"$RALPH_DEST" --version
"$RALPH_DEST" --help
```

If download or verification fails, stop and report the failure. The existing
installation must remain intact on a checksum mismatch. Only `ralph` belongs in
the destination; `SHA256SUMS` is temporary verification metadata.

## 4. Finish setup

For global scope, confirm `command -v ralph` resolves to `~/.local/bin/ralph`.
If needed, add `export PATH="$HOME/.local/bin:$PATH"` to the user's shell startup
file (`~/.zshrc` for zsh, `~/.bashrc` for Bash; macOS login Bash may use
`~/.bash_profile`), and apply it in the current shell. Check `ralph --version`
and `ralph --help` through `PATH`; resolve any older alias or shell function that
shadows the installation. For project scope, run `./ralph --version` and
`./ralph --help` from that project.

Add `.ralph/` to the target project's `.gitignore` if it is not already ignored.
For project scope, ignore the downloaded `/ralph` too. Preserve existing entries.

Use the chosen CLI's existing authentication and model settings. Before a first
loop, the user must run that CLI by hand from the target repository with Ralph's
permission flags, accept its startup/trust dialogs, then exit it:

| Agent | Manual startup |
| --- | --- |
| Codex | `codex --yolo --no-alt-screen` |
| Claude Code | `claude --dangerously-skip-permissions` |
| OpenCode 2.x | `opencode . --standalone --auto` |
| Pi 0.87.0+ | `pi` |

See [agent setup](docs/agents.md#clear-one-time-startup-dialogs-first) for details.
These flags grant the agent the permissions described there; the user handles
interactive login and permission decisions. If those actions are pending, report
them as prerequisites for starting a loop.

Report the installed path, release version, invocation, verification result, and
any remaining agent setup. Installation is complete when the file is executable,
its checksum matched, and the selected invocation passes `--version` and `--help`.
Leave starting a loop for a separate user request.
