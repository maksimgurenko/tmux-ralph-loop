# Installation instructions for an agent

Use this guide when asked to install tmux-ralph-loop for a user's project.
The tool runs directly from its checkout; there is no package build or Python
dependency installation. The same runner and commands work on Linux and macOS;
macOS prerequisite setup is covered separately below.

## 1. Identify the target and prerequisites

Use the user's project repository, ticket directory, and preferred agent from
the task context. Ask for any required values you cannot determine. Keep the
tool checkout separate from the target project.

Check for Python 3.9+, Git, tmux, and the selected agent executable on `PATH`:

```sh
python3 --version
git --version
tmux -V
# Run the version check for the selected CLI only:
codex --version
# claude --version
# opencode --version
# pi --version
```

Codex is the default. OpenCode requires 2.x; Pi requires 0.87.0 or newer.
See [agent setup and compatibility](docs/agents.md) for details. Install missing
prerequisites using the host's package manager and the chosen CLI's official
installation instructions. Only one agent CLI is required. The user must have
authenticated that CLI and configured their desired model; a successful version
check alone does not verify authentication.

### macOS prerequisites

Use an existing Python 3.9+, Git, and tmux installation if available. If any are
missing, install them with [Homebrew](https://docs.brew.sh/Installation):

```sh
# Install only the missing prerequisites:
brew install python git tmux
```

If Homebrew itself is missing, follow its official installation instructions
and the printed shell setup steps. Its usual prefix is `/opt/homebrew` on
Apple Silicon and `/usr/local` on Intel; ensure its `bin` directory is on `PATH`
in the shell that launches the loop. Re-run the prerequisite checks above after
setup, including the selected agent CLI's version check.

The remaining instructions are identical on macOS and Linux. The `ralph` function
below works in zsh and bash; no GNU coreutils or extra Python packages are needed.

## 2. Obtain the complete checkout

Use an existing checkout if one is available. Otherwise:

```sh
git clone https://github.com/maksimgurenko/tmux-ralph-loop.git "$HOME/tmux-ralph-loop"
```

If the destination already exists, inspect it before proceeding; do not overwrite
it or reset local changes. Honor any branch or revision requested by the user.
Keep the entire checkout, including `scripts/adapters/`, which OpenCode and Pi
load at runtime. No extra Python packages or `npm install` are needed for the loop.

## 3. Configure the target project

Replace these example paths and agent with the actual values. Use absolute paths
so subsequent commands target the same project regardless of working directory:

```sh
RALPH_TOOL="$HOME/tmux-ralph-loop"
RALPH_REPO="/absolute/path/to/project"
RALPH_TICKETS="$RALPH_REPO/.scratch/my-feature/issues"
RALPH_AGENT="codex"

git -C "$RALPH_REPO" status --short --branch
python3 "$RALPH_TOOL/scripts/ralph.py" --help
```

Ensure `.ralph/` is ignored in the target project's `.gitignore`, preserving its
existing entries. This directory stores runtime state, prompts, and logs. Leave
existing runtime state intact.

The loop reads local Markdown tickets, not GitHub or Linear issues. Use the
project's existing ticket directory. Each `NN-slug.md` ticket needs exactly one
`**Status:**`, one `**Blocked by:**`, and at least one acceptance checkbox.
Dependencies must reference earlier ticket numbers. See the
[example tickets](examples/issues) for the complete format. Do not copy demo
tickets into the project as real work.

Skills are optional: the built-in prompt works without them. For Matt Pocock's
workflow, follow the [README workflow setup](README.md#from-idea-to-implementation)
and configure its tracker as local Markdown files before generating tickets.

Define a convenience function for the current shell:

```sh
ralph() {
  python3 "$RALPH_TOOL/scripts/ralph.py" "$@" \
    --repo "$RALPH_REPO" --tickets "$RALPH_TICKETS"
}
```

These variables and the function do not persist into a new shell. Include the
resolved values in the handoff; if persistent setup is requested, save them in
the user's chosen shell configuration or project helper without overwriting
existing configuration. Use the same repository, ticket, and state paths on all
later commands. The default state path is `$RALPH_REPO/.ralph`.

## 4. Verify installation

```sh
ralph status
```

Confirm that the expected tickets appear and eligible tickets show `runnable`.
`status` validates ticket structure without launching an agent or consuming model
usage. If tickets do not exist yet, report that the tool is installed but needs
tickets before it can run; do not fabricate tasks to make this check pass.

For an existing run, inspect the reported session and state before resuming it.
Do not delete `.ralph/` to bypass an active or interrupted attempt. See
[recovery instructions](docs/operations.md).

## 5. Start when execution is part of the request

Installation is complete after verification. If the user also requested running
the tickets, start the loop:

```sh
ralph start --agent "$RALPH_AGENT"
ralph status
```

If the chosen CLI has the `implement` skill installed, optionally use:

```sh
ralph start --agent "$RALPH_AGENT" \
  --prompt 'Use the implement skill to implement one eligible ticket.'
```

Starting runs an authenticated agent that consumes model usage and is instructed
to commit completed work on the target project's current branch. Codex and Claude
run with approval bypasses; OpenCode runs in auto mode; Pi uses its normal tool
permissions. See [agent setup](docs/agents.md) for the exact behavior.

Report the tool location, target repository and branch, ticket path, selected
agent, verification result, and whether the loop was started. Provide the exact
commands with resolved paths for `status`, `start`, and `stop`. If a worker is
active, include the **Worker attach** command printed by `status`; the user can
answer setup or blocked-agent questions there and detach with **Ctrl-b, d**.
`ralph stop` lets the current attempt finish and prevents the next dispatch.
