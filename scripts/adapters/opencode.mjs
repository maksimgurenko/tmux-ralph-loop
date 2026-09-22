import { bridge, textContent } from "./bridge.mjs";

export default async function ralphOpenCode({ client, directory }) {
  const reporter = bridge("opencode");
  if (!reporter || !reporter.owns(directory)) return {};
  const versions = new Map();
  async function inspect(event) {
    const id = event.properties?.sessionID;
    if (!id) return;
    if (event.type === "session.status" && event.properties.status.type !== "idle") {
      versions.set(id, (versions.get(id) ?? 0) + 1);
      return;
    }
    if (event.type !== "session.idle" &&
        !(event.type === "session.status" && event.properties.status.type === "idle")) return;
    const version = versions.get(id);
    const options = { path: { id }, query: { directory }, throwOnError: true };
    const { data: session } = await client.session.get(options);
    // Tasks and review agents have child sessions, even if their input is inherited.
    if (!session || session.parentID) return;
    const { data: messages } = await client.session.messages(options);
    if (versions.get(id) !== version || !Array.isArray(messages)) return;
    const first = messages.find((entry) => entry.info.role === "user");
    const last = messages[messages.length - 1];
    if (!first || !last || last.info.role !== "assistant" || last.info.summary ||
        last.info.error || !last.info.time.completed || last.info.finish !== "stop") return;
    reporter.complete(id, last.info.id, textContent(first.parts), textContent(last.parts));
  }
  return {
    event: async ({ event }) => {
      try {
        await inspect(event);
      } catch (error) {
        reporter.failure(error);
      }
    },
  };
}
