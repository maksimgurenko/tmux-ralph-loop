# tmux-ralph-loop

Run **local Markdown tickets, GitHub issues, or GitLab issues** one at a time with
**Codex, Claude Code, OpenCode, or Pi**. The loop starts a fresh agent session for
each attempt and continues until the work is done or your input is needed.

## Why use it?

- **Fresh context:** each new attempt starts a new agent, which chooses an eligible ticket.
- **Resume interruptions:** restart the watcher without launching duplicate work.
- **Inspect progress:** prompts, terminal logs, reports, and runtime events stay on disk.
- **Respect dependencies:** completed blockers and unchanged, checked acceptance criteria gate progress.

## Why tmux?

tmux lets agents use their native interactive CLI. Attach to answer a question,
then detach while the agent continues. Blocked agents keep the same conversation.
The watcher runs independently: stopping or interrupting it preserves the worker,
and restarting it resumes watching that worker.

## Quick start

**[Install Ralph with an agent](INSTALL.agent.md)** as a user-wide `ralph` on `PATH`
or a project-local `./ralph`. The guide downloads one executable from the latest
stable release and verifies its checksum; no source checkout is needed.

Requires **Python 3.9+**, **tmux**, **Git**, and an authenticated agent CLI on `PATH`.
No extra Python packages; tested on Linux and macOS. The agent uses its existing
model and authentication settings.

Before starting, run the agent by hand **from the target project**, accept its
startup dialogs, then exit. Use `codex --yolo --no-alt-screen` for Codex or
`claude --dangerously-skip-permissions` for Claude. The loop cannot answer these
dialogs. See [agent setup](docs/agents.md#clear-one-time-startup-dialogs-first)
for the other CLIs and permission details.

```sh
cd /path/to/your/project
# Add .ralph/ to this project's .gitignore before starting.
# Replace my-feature with your local ticket directory.
ralph start --tickets "$PWD/.scratch/my-feature/issues"
ralph status
tail -f .ralph/loop.log
```

Use `./ralph` for a project installation. Run from the target repository or pass
`--repo /path/to/project`. The agent commits completed work on the current branch
while preserving unrelated changes. Prepare tickets in the [local format](#ticket-format),
or choose a remote source below. Use `ralph -h` or command help such as
`ralph start -h` for options and defaults.

## Choose an issue source

| Source | Start command | Requires |
| --- | --- | --- |
| Local Markdown (default) | `ralph start --tickets .scratch/my-feature/issues` | One file per ticket |
| GitHub | `ralph start --tracker github` | Authenticated `gh` (`gh auth login`) |
| GitLab | `ralph start --tracker gitlab` | Authenticated `glab` (`glab auth login`) |

`gh`/`glab` infer the repository from the checkout. Override it with
`--project owner/repo`, `--project group/subgroup/project`, or a full repository
URL for a self-hosted instance.

Remote sources include only open issues labelled `ready-for-agent` by default.
`--label` replaces that filter; repeated labels must **all** match:

```sh
ralph start --tracker github --label ready-for-agent --label feature:checkout
```

Issues need acceptance checkboxes. All blockers must be closed, including those
outside the filter or in another project on the same host. See
[remote issue formats and dependencies](docs/operations.md#remote-issues).

`start`, `run`, and `retry` save the source, project, ticket directory, and label
filter; `status` only reads them. Later commands can omit the saved options.
To change the source or filter, finish or stop the current worker and use a
separate `--state`, such as `.ralph/github`. Keep the same `--repo` and `--state`
for subsequent commands.

## Choose an agent

Codex is the default. Add `--agent` to select another CLI:

| Agent | Option | Required version |
| --- | --- | --- |
| [Codex](https://developers.openai.com/codex/cli/) | `--agent codex` | — |
| [Claude Code](https://code.claude.com/docs/en/overview) | `--agent claude` | — |
| [OpenCode](https://opencode.ai/v2/docs/) | `--agent opencode` | 2.x only |
| [Pi](https://pi.dev/docs/latest/quickstart) | `--agent pi` | 0.87.0+ |

The chosen agent is remembered across restarts; active runs cannot switch agents.
Codex uses `--yolo`, Claude uses `--dangerously-skip-permissions`, and OpenCode uses
`--auto`; Pi keeps its normal tool permissions. See
[agent compatibility and permissions](docs/agents.md).

## Connect, stop, and resume

Copy the **Worker attach** command from `ralph status`, answer the agent, then
detach with **Ctrl-b, d**. A blocked conversation can continue with a fresh report.

| Command | Effect |
| --- | --- |
| `ralph start` | Start or resume a detached watcher; clear a previous stop request. |
| `ralph run` | Watch in the foreground; the agent still runs in tmux. |
| `ralph status` | Show progress, attach commands, and log location. |
| `ralph stop` | Stop after the current attempt; if already blocked, stop watching and retain the agent. |
| `ralph retry TICKET` | Requeue an unfinished ticket or issue and reset its attempt count. |

**Stopping the watcher, including with Ctrl-C, does not kill the worker.** Use
`start` to resume watching. Timeouts and lost workers stop dispatch without
launching a replacement. Before `retry`, inspect the attempt and stop both watcher
and worker using the [abandonment steps](docs/operations.md#abandoning-an-attempt).
Remote issues use their actual number, such as `ralph retry 123`; retry clears
`ralph:blocked`, the label applied to blocked attempts.

## Customize

The default prompt covers selection, implementation, checks, and review without
installed skills. To replace its task instructions, choose **one** override:

```sh
ralph start --prompt 'Implement one eligible ticket. Run the integration checks.'
ralph start --prompt-file /path/to/instructions.md
```

The loop appends ticket context and reporting rules. Prompt text is literal;
files are read once at startup. Resumed conversations keep their original prompt.
Repeat an override on restart to apply it to future attempts.

Defaults are **3 reported attempts per ticket** and an **8-hour initial autonomous
turn (28800 seconds)**. Override them with:

```sh
ralph run --max-attempts 2 --timeout 3600
```

Assisted follow-ups do not add attempts. Once a blocked report is accepted, the
conversation can wait indefinitely for input. See [configuration](docs/operations.md#configuration).

## From idea to implementation

Install [Matt Pocock’s skills](https://github.com/mattpocock/skills) for your chosen
agent, then run
[`setup-matt-pocock-skills`](https://github.com/mattpocock/skills/blob/main/skills/engineering/setup-matt-pocock-skills/SKILL.md)
in the target project. Choose local Markdown, GitHub, or GitLab to match the loop.

1. [`wayfinder`](https://github.com/mattpocock/skills/blob/main/skills/engineering/wayfinder/SKILL.md): work through the decisions.
2. [`to-spec`](https://github.com/mattpocock/skills/blob/main/skills/engineering/to-spec/SKILL.md): turn them into a specification.
3. [`to-tickets`](https://github.com/mattpocock/skills/blob/main/skills/engineering/to-tickets/SKILL.md): create tickets and blocking dependencies.
4. [`implement`](https://github.com/mattpocock/skills/blob/main/skills/engineering/implement/SKILL.md): implement one eligible ticket per attempt.

Start the loop, adding your source options on the first start:

```sh
ralph start --prompt 'Use the implement skill to implement one eligible ticket.'
```

## Ticket format

Use one `NN-slug.md` file per local ticket, with `**Status:**`, `**Blocked by:**`,
and acceptance checkboxes. Dependencies must link to earlier ticket numbers.
Only `ready-for-agent` tickets with completed blockers are eligible. See the
[example tickets](examples/issues).

Remote issues need acceptance checkboxes and native dependencies or an explicit
`Blocked by` field. The agent updates checkboxes and evidence in the issue body;
the runner manages issue state and the `ralph:blocked` label.

Completion requires a fresh report, its matching turn-end event, and the original
acceptance criteria checked without changes to their text. The runner then updates
local status or closes the remote issue. **It does not rerun tests or independently
verify correctness.**

## Development

### Tests

Run from the source checkout:

```sh
python3 -m unittest discover -s tests -v
```

Tests use real tmux with simulated agents and trackers, without model API calls
or changes to real issues. OpenCode/Pi adapter tests also need Node.js.

Live tests require all four installed, authenticated CLIs and use their configured
models, **consuming model usage**. They are skipped unless you opt in:

```sh
RALPH_LIVE_TESTS=1 python3 -m unittest discover -s tests -p test_live_agents.py -v
```

Missing CLIs, authentication or startup errors, blocked reports, and timeouts fail
the opted-in tests. Artifacts remain in `.ralph/live-tests/`; see the
[live-test harness](tests/test_live_agents.py) for individual-agent tests and settings.

### Build and release

```sh
python3 scripts/build.py --version v1.2.3
```

The [builder](scripts/build.py) writes `dist/ralph` and `dist/SHA256SUMS` using the
standard library; omit `--version` for `dev`. Source execution with
`python3 scripts/ralph.py` requires the complete checkout.

The [CI workflow](.github/workflows/ci.yml) tests source and built executable on
Linux and macOS. A `vX.Y.Z` tag publishes `ralph` and `SHA256SUMS` after checks pass.
Wait for its `publish` job before downloading.

[MIT licensed](LICENSE).
