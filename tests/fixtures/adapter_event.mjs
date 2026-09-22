// Exercise the shipped adapters with native runtime-shaped fixtures, without a model.
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const input = JSON.parse(readFileSync(0, "utf8"));
const { default: adapter } = await import(pathToFileURL(input.adapter));
if (input.agent === "opencode") {
  const hooks = await adapter({ directory: input.cwd, client: { session: {
    get: async () => ({ data: input.session }),
    messages: async () => {
      if (input.failure) throw new Error("Simulated API read failure");
      if (input.busyDuringRead) await hooks.event({ event: {
        type: "session.status", properties: { sessionID: input.session.id, status: { type: "busy" } },
      } });
      return { data: input.messages };
    },
  } } });
  if (hooks.event) await hooks.event({ event: input.event });
} else {
  const handlers = new Map();
  adapter({ on: (name, fn) => handlers.set(name, fn) });
  const ctx = {
    cwd: input.cwd,
    isIdle: () => input.idle ?? true,
    hasPendingMessages: () => input.pending ?? false,
    sessionManager: {
      getSessionFile: () => input.file,
      getSessionId: () => input.session.id,
      getBranch: () => input.entries,
    },
  };
  const handler = handlers.get(input.event.type);
  if (handler) await handler(input.event, ctx);
}
