# Recovery and runtime details

Use the same `--repo`, `--tickets`, and `--state` values on every command. The
examples below assume the `ralph` shell function in the README and default state.

## Configuration

| Option | Default |
| --- | --- |
| `--repo` | Current directory |
| `--tickets` | `<repo>/issues` |
| `--state` | `<repo>/.ralph` |
| `--max-attempts` | `3` reported attempts per ticket |
| `--timeout` | `28800` seconds (eight hours) for the initial autonomous turn |

Explicit relative paths resolve from the directory where you invoke the command.
Provide either `--prompt` or `--prompt-file` to override task instructions. New
instructions apply to future attempts, while a resumed conversation retains its
original prompt. The runner overrides Codex's notification hook for each invocation
without changing your global config.

`start` runs the watcher in a detached tmux session and clears a graceful-stop
request. `run` watches in the foreground; remove `.ralph/STOP` to resume dispatch
after a previous `stop` when using `run`.

Ticket statuses are `ready-for-agent`, `in-progress`, `blocked`, and `done`.
Use `**Blocked by:** None (can start immediately).` when there are no dependencies;
otherwise use links in the form `[01: Title](01-slug.md)`. Each ticket must have at
least one acceptance checkbox. A ticket marked done with unchecked criteria is rejected.

## Answering a blocked agent

1. Run `ralph status` and copy its **Worker attach** command.
2. Attach and answer the agent normally. It can report a fresh result in the same conversation.
3. Detach with **Ctrl-b, d**. If the watcher was stopped, restart it with `ralph start`
   and your usual prompt override for future attempts.

The watcher binds completion to the run’s original input, working directory,
conversation, and unique report marker. Unrelated notifications, reviewer replies,
and reused markers cannot advance the loop. A report alone is insufficient: its
matching turn-end event must arrive. If an agent forgets its marker, attach and
ask it to submit a fresh report and finish its turn with the printed marker.

The initial timeout still applies until the watcher accepts a blocked report.
After that, the live conversation can wait for operator input indefinitely.

## Abandoning an attempt

A timeout or lost/dead worker stops the watcher without automatically dispatching
replacement work. Inspect the retained session and logs first.

To abandon it deliberately:

1. Use `ralph stop`. If the watcher is still waiting on an autonomous turn, stop
   the foreground watcher with Ctrl-C or use the **Runner attach** command from
   `status` and press Ctrl-C there. Wait for the watcher to exit.
2. Use `status` to identify the owned worker. Stop that session with
   `tmux -L <socket> kill-session -t <worker-session>` and verify any commands it
   started have stopped. Killing the session alone does not prove all child work stopped.
3. Run `ralph retry 02` for the unfinished ticket, then `ralph start` with your
   usual prompt override.

`retry` refuses while the recorded worker session exists. Never delete runtime
state to bypass an uncertain attempt. Restart cannot recover a lost Codex
conversation or prove an external action did not happen.

## Retained state

The default `.ralph/` directory contains:

- `loop.log`: detached watcher output.
- `state.json`: active run and attempt counts.
- `runs/<run-id>/`: assembled prompt, ticket snapshot, runtime events, immutable
  per-turn reports, terminal output, and progress/outcome records.
- `STOP`: graceful stop request, cleared by `start`.
- `runner.lock`: repository-wide lock, used even with a custom `--state` directory.

Successful and retrying workers close after accepted turn completion; their logs
remain. A blocked report or exhausted retry budget preserves the worker for
input. Assisted follow-up turns do not consume additional attempts.

Ticket checkboxes and evidence are committed by the agent before reporting;
the runner writes the ticket’s final status afterward, so that status update may
remain uncommitted. The runner does not independently verify the reported commit
or rerun the ticket’s tests.

Each repository gets a dedicated tmux socket. The lock prevents concurrent
watchers in this tool; it does not coordinate with older copies of the runner.
Existing sessions and state from the original project are not migrated. Run one automation tool
at a time against a working tree.
