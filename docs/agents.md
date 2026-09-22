# Agent setup and compatibility

Install and authenticate the CLI you want to use, configure its preferred model,
then select it with `--agent`. Only the selected CLI needs to be installed. Every
agent runs in an interactive tmux terminal: attach using `status`, answer questions,
and detach while it continues. The loop does not use print, headless, or RPC modes.

Keep the complete tool checkout: OpenCode and Pi load the bundled JavaScript adapters
from `scripts/adapters/`. They use their runtime's built-in Node-compatible APIs;
there is no additional package installation for the loop.

## Codex

`--agent codex` launches `codex --yolo --no-alt-screen`. Approval prompts and the
sandbox are disabled. The existing model and login settings are inherited; the
per-invocation `notify` override supplies turn-end events. Existing Codex checkpoints
remain compatible.

## Claude Code

`--agent claude` launches `claude --dangerously-skip-permissions` with a fresh
session UUID. It supplies an additional settings file inside the run directory;
your user and project settings are not edited.

The adapter records the original input through `UserPromptSubmit` and reads the
final assistant message from `Stop`. It accepts only the allocated main session
and working directory, ignoring subagent and failure events. Raw hook payloads
remain under `native-events/`. Other configured hooks remain installed; hooks
that disable or indefinitely block stopping can prevent automatic progress.

See the [CLI reference](https://code.claude.com/docs/en/cli-reference) and
[hook reference](https://code.claude.com/docs/en/hooks#stop).

## OpenCode

`--agent opencode` launches the TUI with `--auto`, which automatically approves
permissions except those explicitly denied. A bundled plugin is appended through
the process's `OPENCODE_CONFIG_CONTENT`; existing inline settings and plugins are
preserved. An existing value of that variable must be valid JSON.

On an idle event, the plugin reads the native session and messages through the
OpenCode SDK. It rejects child sessions, summaries, errors, incomplete/tool-call
responses, and unrelated initial prompts. Only a completed final response carrying
the run's fresh report marker can advance the loop.

See [CLI flags](https://opencode.ai/docs/cli/#tui),
[configuration precedence](https://opencode.ai/docs/config/), and
[plugins](https://opencode.ai/docs/plugins/).

## Pi

`--agent pi` uses [Pi 0.87.0+](https://pi.dev/docs/latest/quickstart), distributed as
`@earendil-works/pi-coding-agent`. Older installations, including the legacy
`@mariozechner/pi-coding-agent` release, receive an upgrade message before dispatch.
The runner uses Pi's normal tool permissions; it does not add an approval bypass.
Project-trust or extension prompts may still need an answer through tmux.

Each run gets a dedicated session file and an explicitly loaded extension. The
extension waits for `agent_settled`, after automatic retry, compaction, and queued
continuations, rather than the earlier `agent_end`. It verifies the session file,
working directory, original input, and final assistant response.

See [Pi extensions](https://pi.dev/docs/latest/extensions).

## Prompts and validation

The generic default prompt works with every agent. For portable skill use, supply
plain-language instructions such as `--prompt 'Use the implement skill to implement
one eligible ticket.'` and install that skill for the chosen CLI. Codex's
`$implement` convention is specific to Codex. CLI slash commands or attachment
expansion may rewrite the initial input and therefore fail the exact-prompt check.

All adapters retain the same completion gate: a fresh report, its matching runtime
completion event, an eligible ticket, and unchanged, checked acceptance criteria.
Lost sessions or missing callbacks do not cause automatic replacement work.
OpenCode/Pi callback failures are retained in `adapter-errors.log` inside the run
directory and leave the interactive session available for inspection.

Automated tests exercise native-shaped events and real tmux with simulated CLIs,
including blocked-agent continuation. Native CLI transport checks use an isolated
local simulated model endpoint; they verify integration rather than model quality.
Validated with Codex 0.155.1 (simulated CLI integration), Claude Code 2.1.278,
OpenCode 1.18.32, and Pi 0.87.0. The three new CLIs each completed a blocked turn
and an operator-assisted follow-up through their real interactive runtime.
