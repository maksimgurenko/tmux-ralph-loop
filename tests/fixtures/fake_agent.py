"""Fake interactive CLIs that execute the real hook/extension adapters."""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from urllib.parse import unquote, urlparse

if sys.argv[1:] == ["--version"]:
    print("opencode v2.0.14" if Path(sys.argv[0]).name == "opencode" else "0.87.0")
    sys.exit(0)

run = Path(os.environ["TMUX_RALPH_RUN"])
meta = json.loads((run / "run.json").read_text())
agent = meta["agent"]
parser = argparse.ArgumentParser()
if agent == "claude":
    parser.add_argument("--dangerously-skip-permissions", action="store_true")
    parser.add_argument("--session-id")
    parser.add_argument("--settings", type=Path)
    parser.add_argument("prompt")
elif agent == "opencode":
    parser.add_argument("project")
    parser.add_argument("--auto", action="store_true")
    parser.add_argument("--standalone", action="store_true")
    parser.add_argument("--prompt")
else:
    parser.add_argument("--session")
    parser.add_argument("--extension")
    parser.add_argument("prompt")
args = parser.parse_args()
assert args.prompt == meta["prompt"]
assert str(Path.cwd()) == meta["repo"]
callback = meta["notify"]
fixture = Path(os.environ["RALPH_TEST_ADAPTER_FIXTURE"])


def claude_hook(kind, **fields):
    settings = json.loads(args.settings.read_text())
    command = settings["hooks"][kind][0]["hooks"][0]["command"]
    event = dict(hook_event_name=kind, session_id=args.session_id, cwd=meta["repo"], **fields)
    subprocess.run(shlex.split(command), input=json.dumps(event), text=True, check=True)


def finish(outcome, turn):
    if outcome == "done":
        ticket.write_text(ticket.read_text().replace("[ ]", "[x]"))
    result = subprocess.run([*callback[:2], "_report", str(run), outcome, number, "Simulated result"],
                            check=True, capture_output=True, text=True)
    message = result.stdout.strip()
    if agent == "claude":
        claude_hook("Stop", last_assistant_message=message, stop_hook_active=False)
        return
    text = lambda value: [{"type": "text", "text": value}]
    payload = dict(agent=agent, cwd=meta["repo"], session={"id": run.name})
    if agent == "opencode":
        assert args.project == meta["repo"] and args.auto and args.standalone
        config = json.loads(os.environ["OPENCODE_CONFIG_CONTENT"])
        payload["session"].update(location={"directory": meta["repo"]}, outcome="succeeded")
        payload.update(adapter=str(Path(unquote(urlparse(config["plugins"][-1]).path)) / "index.js"),
                       event={"type": "session.status", "data": {
                           "sessionID": run.name, "status": {"type": "idle"}}},
                       messages=[{"type": "user", "text": meta["prompt"]},
                                 {"type": "assistant", "id": turn, "time": {"completed": 1},
                                  "finish": "stop", "content": text(message)},
                                 {"type": "idle", "outcome": "succeeded"}])
    else:
        payload.update(adapter=args.extension, file=args.session, event={"type": "agent_settled"},
                       entries=[{"type": "message", "id": "user", "message": {
                           "role": "user", "content": text(meta["prompt"])}},
                                {"type": "message", "id": turn, "message": {
                                    "role": "assistant", "content": text(message), "stopReason": "stop"}}])
    subprocess.run(["node", str(fixture)], input=json.dumps(payload), text=True, check=True)


eligible = [n for n, t in meta["tickets"].items() if t["status"] == "ready-for-agent"
            and all(meta["tickets"][b]["status"] == "done" for b in t["blockers"])]
number = sorted(eligible)[0]
ticket = Path(meta["tickets"][number]["path"])
if agent == "claude":
    assert args.session_id == meta["runtime_session_id"] and args.dangerously_skip_permissions
    claude_hook("UserPromptSubmit", prompt=args.prompt)
interactive = os.environ.get("RALPH_TEST_OUTCOME") == "interactive"
finish("blocked" if interactive else "done", "one")
if interactive:
    followup = input("Blocked. Supply instructions: ")
    if agent == "claude":
        claude_hook("UserPromptSubmit", prompt=followup)
    finish("done", "two")
