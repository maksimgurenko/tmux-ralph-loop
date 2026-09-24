import { bridge, textContent } from "./bridge.mjs";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

// V2's Plugin.define is an identity helper; exporting the definition directly
// keeps this local plugin dependency-free, including under the Node test runner.
export default {
  id: "tmux-ralph-loop",
  setup(ctx) {
    const reporter = bridge("opencode");
    if (!reporter || !reporter.owns(ctx.location.directory)) return;
    writeFileSync(join(reporter.run, "opencode-ready"), "V2 plugin loaded\n");
    const controller = new AbortController();
    const versions = new Map();
    const pending = new Set();
    const firstInputs = new Map();
    const originPath = join(reporter.run, "opencode-origins.json");
    const origins = existsSync(originPath) ? JSON.parse(readFileSync(originPath, "utf8")) : {};

    async function inspect(id, version) {
      const session = await ctx.session.get({ sessionID: id });
      // Plugin location alone does not establish the location of each session.
      if (!session || session.id !== id || session.parentID || session.fork ||
          !reporter.owns(session.location?.directory) || session.outcome !== "succeeded") return;
      const messages = await ctx.session.context({ sessionID: id });
      if (controller.signal.aborted || versions.get(id) !== version || !Array.isArray(messages)) return;
      const firstIndex = messages.findIndex((entry) => entry.type === "user");
      const first = messages[firstIndex];
      const idle = messages[messages.length - 1];
      const last = messages[messages.length - 2];
      // A delivered first input remains provable after compaction/plugin reload.
      // Without that evidence, require the original, uncompacted user context.
      const original = origins[id] || (first?.text === reporter.meta.prompt &&
        !messages.slice(0, firstIndex).some((entry) => entry.type === "compaction"));
      // V2 appends a durable idle marker after all queued work and tools settle.
      if (!original || idle?.type !== "idle" || idle.outcome !== "succeeded" ||
          last?.type !== "assistant" || last.error || !last.time?.completed || last.finish !== "stop") return;
      reporter.complete(id, last.id, reporter.meta.prompt, textContent(last.content));
    }

    const listening = (async () => {
      try {
        for await (const event of ctx.event.subscribe({ signal: controller.signal })) {
          const id = event.data?.sessionID;
          if (!id) continue;
          if (event.type === "session.created") firstInputs.set(id, null);
          if (event.type === "session.inbox.enqueued" && event.data.item?.type === "user" &&
              firstInputs.get(id) === null) {
            firstInputs.set(id, { id: event.data.inboxID, text: event.data.item.payload.text });
          }
          const first = firstInputs.get(id);
          if (event.type === "session.inbox.delivered" && first?.id && first.id === event.data.inboxID &&
              first.text === reporter.meta.prompt) {
            origins[id] = first.id;
            writeFileSync(originPath, JSON.stringify(origins));
          }
          if (event.type === "session.inbox.enqueued" && event.data.item?.type === "user" &&
              event.data.item.payload.text === reporter.meta.prompt) {
            writeFileSync(join(reporter.run, "opencode-input.json"), JSON.stringify({ sessionID: id }));
          }
          if (event.type === "session.execution.started" || event.type === "session.inbox.enqueued" ||
              (event.type === "session.status" && event.data.status?.type !== "idle")) {
            versions.set(id, (versions.get(id) ?? 0) + 1);
          }
          if (event.type !== "session.execution.succeeded" &&
              !(event.type === "session.status" && event.data.status?.type === "idle")) continue;
          // Keep consuming the stream during API reads so newer busy events
          // invalidate stale observations instead of advancing the loop early.
          const task = inspect(id, versions.get(id)).catch((error) => reporter.failure(error));
          pending.add(task);
          void task.finally(() => pending.delete(task));
        }
      } catch (error) {
        if (!controller.signal.aborted) reporter.failure(error);
      }
    })();
    return async () => {
      controller.abort();
      await listening;
      await Promise.all(pending);
    };
  },
};
