import { appendFileSync, readFileSync, realpathSync } from "node:fs";
import { join } from "node:path";
import { spawnSync } from "node:child_process";

export function textContent(content) {
  if (typeof content === "string") return content;
  return (content ?? []).filter((part) => part.type === "text").map((part) => part.text).join("\n");
}

export function bridge(agent) {
  const run = process.env.TMUX_RALPH_RUN;
  if (!run) return null;
  const meta = JSON.parse(readFileSync(join(run, "run.json"), "utf8"));
  if (meta.agent !== agent) return null;
  return {
    run,
    meta,
    owns(cwd) {
      return typeof cwd === "string" && realpathSync(cwd) === realpathSync(meta.repo);
    },
    failure(error) {
      const message = `Ralph adapter error: ${error?.stack ?? error}`;
      console.error(message);
      try {
        appendFileSync(join(run, "adapter-errors.log"), `${new Date().toISOString()} ${message}\n`);
      } catch {
        // A logging failure must not crash the interactive agent either.
      }
    },
    complete(thread, turn, firstPrompt, message) {
      if (!thread || !turn || firstPrompt !== meta.prompt) return;
      const event = {
        type: "agent-turn-complete", agent, cwd: meta.repo,
        "thread-id": thread, "turn-id": turn,
        "input-messages": [firstPrompt], "last-assistant-message": message,
      };
      const result = spawnSync(meta.notify[0], meta.notify.slice(1), {
        input: JSON.stringify(event), encoding: "utf8", timeout: 10000,
      });
      if (result.error || result.status !== 0) {
        throw new Error(`Ralph notification failed: ${result.error ?? result.stderr}`);
      }
    },
  };
}
