import { resolve, join } from "node:path";
import { bridge, textContent } from "./bridge.mjs";

export default function ralphPi(pi) {
  const reporter = bridge("pi");
  if (!reporter) return;
  const sessionFile = join(reporter.run, "pi-session.jsonl");
  // agent_end is too early: Pi may automatically retry or compact afterward.
  function inspect(ctx) {
    if (!ctx.isIdle() || ctx.hasPendingMessages() || !reporter.owns(ctx.cwd) ||
        resolve(ctx.sessionManager.getSessionFile() ?? "") !== sessionFile) return;
    const entries = ctx.sessionManager.getBranch().filter((entry) => entry.type === "message");
    const first = entries.find((entry) => entry.message.role === "user");
    const last = entries[entries.length - 1];
    if (!first || !last || last.message.role !== "assistant" || last.message.stopReason !== "stop") return;
    reporter.complete(ctx.sessionManager.getSessionId(), last.id,
      textContent(first.message.content), textContent(last.message.content));
  }
  pi.on("agent_settled", (_event, ctx) => {
    try {
      inspect(ctx);
    } catch (error) {
      reporter.failure(error);
    }
  });
}
