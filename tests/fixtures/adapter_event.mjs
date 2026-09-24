// Exercise the shipped adapters with native runtime-shaped fixtures, without a model.
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const input = JSON.parse(readFileSync(0, "utf8"));
const { default: adapter } = await import(pathToFileURL(input.adapter));
if (input.agent === "opencode") {
  const events = [...(input.events ?? [input.event])];
  let wake = () => {};
  const cleanup = await adapter.setup({ location: { directory: input.cwd }, event: {
    async *subscribe({ signal }) {
      signal.addEventListener("abort", () => wake(), { once: true });
      while (!signal.aborted) {
        if (events.length) yield events.shift();
        else await new Promise((resolve) => { wake = resolve; });
      }
    },
  }, session: {
    get: async ({ sessionID }) => {
      if (sessionID !== input.session.id) throw new Error("Wrong V2 session ID");
      return input.session;
    },
    context: async ({ sessionID }) => {
      if (sessionID !== input.session.id) throw new Error("Wrong V2 session ID");
      if (input.failure) throw new Error("Simulated API read failure");
      if (input.busyDuringRead) {
        events.push({ type: "session.status", data: { sessionID: input.session.id, status: { type: "busy" } } });
        wake();
        await new Promise(setImmediate);
      }
      return input.messages;
    },
  } });
  // Let API reads and any concurrent busy event settle, then exercise teardown.
  await new Promise(setImmediate);
  await new Promise(setImmediate);
  if (cleanup) await cleanup();
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
