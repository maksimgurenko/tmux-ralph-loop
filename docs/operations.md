# Recovery and runtime details

Use the same `--repo` and `--state` values on every command. The issue source and
ticket directory are remembered after `start`, `run`, or `retry`. The
examples below assume the `ralph` shell function in the README and default state.

## Configuration

| Option | Default |
| --- | --- |
| `--agent` | Recorded agent, or `codex` for a new state directory |
| `--repo` | Current directory |
| `--tracker` | Recorded source, or `local`; also supports `github` and `gitlab` |
| `--project` | Recorded remote project, otherwise inferred by `gh`/`glab` |
| `--label` | Recorded filter, otherwise `ready-for-agent`; repeat to require all labels |
| `--tickets` | Recorded local directory, otherwise `<repo>/issues`; local tracker only |
| `--state` | `<repo>/.ralph` |
| `--max-attempts` | `3` reported attempts per ticket |
| `--timeout` | `28800` seconds (eight hours) for the initial autonomous turn |

Explicit relative paths resolve from the directory where you invoke the command.
Provide either `--prompt` or `--prompt-file` to override task instructions. New
instructions apply to future attempts, while a resumed conversation retains its
original prompt. Agent hooks and extensions are configured for each invocation
without changing your global config; see [agent compatibility](agents.md).

The chosen agent is recorded with each run and in the checkpoint. Restarting without
`--agent` keeps it. A different `--agent` is rejected while a run is active. After
finishing or explicitly abandoning/requeueing that run, you can select another CLI.
Older checkpoints without an agent field are treated as Codex runs.

`start` runs the watcher in a detached tmux session and clears a graceful-stop
request. `run` watches in the foreground; remove `.ralph/STOP` to resume dispatch
after a previous `stop` when using `run`.

Local ticket statuses are `ready-for-agent`, `in-progress`, `blocked`, and `done`.
Use `**Blocked by:** None (can start immediately).` when there are no dependencies;
otherwise use links in the form `[01: Title](01-slug.md)`. Each ticket must have at
least one acceptance checkbox. A ticket marked done with unchecked criteria is rejected.

## Remote issues

The `github` tracker uses the authenticated `gh` CLI; `gitlab` uses `glab`.
Both read every page of open issues matching all configured labels. GitHub pull
requests are excluded. Remote issues use their native number, without `#` or
zero-padding, when reporting or retrying. Each attempt retains its issue bodies,
URLs, and dependency state in `runs/<run-id>/issues.json`.

An issue is eligible if it matches the label filter, has no `ralph:blocked` label,
and all its blockers are closed. Dependencies can be native GitHub issue
dependencies, GitLab `is_blocked_by` links, or references in either form below:

```markdown
Blocked by: #123, other/project#456
```

```markdown
## Blocked by
- #123
- https://gitlab.example.com/group/project/-/issues/456
```

`**Blocked by:**` is also supported. Use `None` when an explicit field has no
blockers. References outside the selected labels or project are fetched directly;
text references must point to the same host and tracker. Parent references and
ordinary mentions elsewhere in the body are not dependencies. Native links and
text references are both checked when present. If a server lacks native dependency
endpoints (HTTP 404/410), an explicit `Blocked by` field is required as a fallback.
Access and network failures stop the watcher with the current run retained.

The agent reads current issue details/comments and edits acceptance checkboxes
and evidence in the issue body. It leaves state and the `ralph:blocked` label to
the runner. Completed reports require the original checked criteria and closed
blockers to pass another remote read before closing. The runner posts no comments.
Blocking adds `ralph:blocked` while retaining the scope labels; retry removes it,
and completion clears it and closes the issue. The GitHub label is created on
first use; the CLI needs permission to create/apply labels and edit issues.

If no open issues match the filter, the loop exits successfully. If matching
issues exist but all are blocked, it exits without launching an agent. Restart
after resolving their blockers or use `retry` for an abandoned attempt. An active
blocked agent can still accept your input in its existing conversation.

The source and filter are bound to a state directory to prevent attempts from
different projects sharing number-based accounting. To change them, finish or
stop the current worker and select a different `--state`. The repository lock
still prevents concurrent watchers in the same working tree. Separate clones
and other people are not coordinated by this lock.
Older local checkpoints with recorded attempts also need a separate `--state`
when switching to remote issues.

## Answering a blocked agent

1. Run `ralph status` and copy its **Worker attach** command.
2. Attach and answer the agent normally. It can report a fresh result in the same conversation.
3. Detach with **Ctrl-b, d**. If the watcher was stopped, restart it with `ralph start`
   and your usual prompt override for future attempts.

The watcher binds completion to the run’s agent, original input, working directory,
conversation, and unique report marker. Unrelated notifications, reviewer replies,
and reused markers cannot advance the loop. A report alone is insufficient: its
matching turn-end event must arrive. If an agent forgets its marker, attach and
ask it to submit a fresh report and finish its turn with the printed marker.

The initial timeout still applies until the watcher accepts a blocked report.
After that, the live conversation can wait for operator input indefinitely.

## Abandoning an attempt

A timeout or lost/dead worker stops the watcher without automatically dispatching
replacement work. Inspect the retained session and logs first. A first attempt that
times out having done nothing is usually an unanswered startup dialog rather than a
stuck model; the watcher names recognized ones, and
[agent setup](agents.md#clear-one-time-startup-dialogs-first) covers clearing them.

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
- `source.json`: tracker, project or local directory, and label filter.
- `runs/<run-id>/`: assembled prompt, ticket snapshot, runtime events, immutable
  per-turn reports, terminal output, agent-specific hook/session files, and progress/outcome records.
- `STOP`: graceful stop request, cleared by `start`.
- `runner.lock`: repository-wide lock, used even with a custom `--state` directory.

Successful and retrying workers close after accepted turn completion; their logs
remain. A blocked report or exhausted retry budget preserves the worker for
input. Assisted follow-up turns do not consume additional attempts.

Local ticket checkboxes and evidence are committed by the agent before reporting;
the runner writes the ticket’s final status afterward, so that status update may
remain uncommitted. The runner does not independently verify the reported commit
or rerun the ticket’s tests.

Each repository gets a dedicated tmux socket. The lock prevents concurrent
watchers in this tool; it does not coordinate with older copies of the runner.
Existing sessions and state from the original project are not migrated. Run one automation tool
at a time against a working tree.
