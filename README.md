# tmux-ralph-loop

A resumable ticket loop that runs interactive Codex agents in tmux.

Developed specifically for [Matt Pocock’s skill workflow](https://github.com/mattpocock/skills):
plan the work, produce a spec, break it into tickets, then let the loop drive
implementation. It automates opening a session, running
[`implement`](https://github.com/mattpocock/skills/blob/main/skills/engineering/implement/SKILL.md),
waiting for it to finish, and opening the next session—until the tickets are done
or your input is needed.

**Agents can mark themselves blocked when they need something from you.** Because
they run interactively inside tmux, you can attach to their session, answer
questions, and unblock the same agent without starting over.

## Why use it?

- **Fresh context:** each new attempt starts a new agent; the agent chooses an eligible ticket.
- **Resume after interruptions:** restart the watcher without launching duplicate work.
- **Inspect progress:** keep prompts, terminal logs, reports, and runtime events on disk.
- **Keep dependencies in order:** completed blockers and unchanged, checked acceptance criteria gate progress.

## From idea to implementation

Install [Matt’s skills](https://github.com/mattpocock/skills#installation-30-second-setup)
and run [`setup-matt-pocock-skills`](https://github.com/mattpocock/skills/blob/main/skills/engineering/setup-matt-pocock-skills/SKILL.md)
for your project, choosing **local Markdown files** as the tracker.

1. Run [`wayfinder`](https://github.com/mattpocock/skills/blob/main/skills/engineering/wayfinder/SKILL.md)
   over a few sessions to work through the decisions until the path is clear.
2. Use [`to-spec`](https://github.com/mattpocock/skills/blob/main/skills/engineering/to-spec/SKILL.md)
   to turn the resolved decisions into a specification.
3. Use [`to-tickets`](https://github.com/mattpocock/skills/blob/main/skills/engineering/to-tickets/SKILL.md)
   to create numbered, dependency-linked tickets under `.scratch/<feature>/issues/`.
4. Start this loop with the skill prompt below. It handles the repeated implementation sessions.

This version reads local ticket files; it does not fetch GitHub or Linear issues.

## Quick start

Requires **Python 3.9+**, **tmux**, **Git**, and an authenticated **Codex CLI** on
your `PATH`. Uses only Python’s standard library; tested on Linux.

```sh
git clone https://github.com/maksimgurenko/tmux-ralph-loop.git "$HOME/tmux-ralph-loop"
cd /path/to/your/project
# Add .ralph/ to this project's .gitignore before starting.

# Replace my-feature with the directory generated for your tickets.
ralph() {
  python3 "$HOME/tmux-ralph-loop/scripts/ralph.py" "$@" \
    --tickets "$PWD/.scratch/my-feature/issues"
}

ralph status
ralph start --prompt '$implement Choose one eligible unfinished ticket and implement it.'
tail -f .ralph/loop.log
```

Run these commands from the target repository. Codex inherits your authentication
and model settings. The runner invokes
`codex --yolo` (approval prompts and sandbox disabled) and instructs the agent to
commit completed ticket work on the current branch while preserving unrelated changes.

## Connect, stop, and resume

`ralph status` prints the exact **Worker attach** command. Run it to connect to the
agent, answer its questions or complete a setup dialog, then detach with **Ctrl-b, d**.
A blocked agent keeps its conversation; the loop waits for a fresh completion report.

| Command | Effect |
| --- | --- |
| `ralph status` | Show tickets, active session, attach command, and log location. |
| `ralph run --prompt '…'` | Watch in the foreground; the agent still runs in tmux. |
| `ralph stop` | Finish the current attempt, then dispatch no more work. If already blocked, stop watching and retain the agent. |
| `ralph start --prompt '…'` | Start or resume watching; clear a previous stop request. Repeat your custom prompt for future attempts. |
| `ralph retry 02` | Explicitly requeue an unfinished ticket and reset its attempt budget, after stopping the watcher and any active worker. |

Ctrl-C preserves the worker. Timeouts and lost workers stop dispatch.
See [configuration and recovery details](docs/operations.md) for limits, state,
and abandoning an attempt.

## Customize

Without a prompt override, built-in instructions cover selection, implementation,
checks, and review; no skill installation is required. Customize task instructions
with **one** of:

```sh
ralph start --prompt 'Implement one eligible ticket. Run the integration checks.'
ralph start --prompt-file /path/to/instructions.md
```

The loop appends ticket context and reporting rules. Prompt text is literal, and
files are read once at startup. Resumed conversations keep their original prompt.

Defaults: `--repo` is the current directory, `--tickets` is `<repo>/issues`, and
`--state` is `<repo>/.ralph`. Use the same ticket/state paths on later commands.

## Ticket format

Use one `NN-slug.md` file per ticket, with `**Status:**`, `**Blocked by:**`, and
acceptance checkboxes, as shown in the [two example tickets](examples/issues).
Dependencies must link to earlier ticket numbers. Only `ready-for-agent` tickets
with completed blockers are eligible. The runner updates status after a matching
report and turn-end event; completion also requires unchanged, checked acceptance
criteria. These checks validate reported completion, not independently prove correctness.

## Tests and license

```sh
python3 -m unittest discover -s tests -p 'test_ralph.py' -v
```

Run from this tool’s checkout. Integration tests use real tmux with simulated
Codex; they do not make model API calls. [MIT licensed](LICENSE).
