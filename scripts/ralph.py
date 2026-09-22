#!/usr/bin/env python3
"""Serial, resumable ticket runner using interactive Codex in tmux (stdlib only)."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
import uuid

SCRIPT = Path(__file__).resolve()
DEFAULT_PROMPT = """Follow the repository's instructions. Choose one eligible unfinished
ticket, implement it, and run the relevant checks. Review your changes and record
verification evidence in the ticket before reporting completion."""
STATUS = re.compile(r"^\*\*Status:\*\* (\S+)[ \t]*$", re.M)
CHECK = re.compile(r"^- \[([ xX])\] (.+)$", re.M)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temp.open("x") as stream:
        stream.write(value if isinstance(value, str) else json.dumps(value, indent=2) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def read(path):
    return json.loads(path.read_text())


def tickets(folder):
    found = {}
    for path in sorted(folder.glob("[0-9][0-9]-*.md")):
        body = path.read_text()
        number = path.name[:2]
        statuses = STATUS.findall(body)
        edges = re.findall(r"^\*\*Blocked by:\*\* (.+)$", body, re.M)
        if number in found or len(statuses) != 1 or len(edges) != 1 or not CHECK.findall(body):
            raise ValueError(f"Malformed or duplicate ticket: {path}")
        if statuses[0] not in {"ready-for-agent", "in-progress", "blocked", "done"}:
            raise ValueError(f"Unknown ticket status: {path}")
        blockers = re.findall(r"\[(\d{2}): [^\]]+\]\([^\)]+\)", edges[0])
        if not blockers and not edges[0].startswith("None (can start immediately)"):
            raise ValueError(f"Unrecognized blockers: {path}")
        found[number] = dict(path=path, body=body, status=statuses[0], blockers=blockers)
    if not found:
        raise ValueError(f"No tickets found in {folder}")
    for number, ticket in found.items():
        if any(b not in found or b >= number for b in ticket["blockers"]):
            raise ValueError(f"Ticket {number} has missing or non-earlier blockers")
        if ticket["status"] == "done" and any(x == " " for x, _ in CHECK.findall(ticket["body"])):
            raise ValueError(f"Ticket {number} claims done with unchecked criteria")
    return found


def frontier(found):
    return [n for n, t in found.items() if t["status"] == "ready-for-agent"
            and all(found[b]["status"] == "done" for b in t["blockers"])]


def mark(path, status):
    body, count = STATUS.subn(f"**Status:** {status}", path.read_text())
    if count != 1:
        raise ValueError(f"Missing/duplicate Status in {path}")
    write(path, body)


def socket(repo):
    return "tmux-ralph-loop-" + hashlib.sha256(str(repo).encode()).hexdigest()[:10]


def tmux(repo, *args, check=True):
    return subprocess.run(["tmux", "-L", socket(repo), "-f", "/dev/null", *args],
                          text=True, capture_output=True, check=check)


def exists(repo, session):
    return tmux(repo, "has-session", "-t", "=" + session, check=False).returncode == 0


def report_marker(event):
    match = re.search(r"(?:^|\n)RALPH_REPORT:([a-f0-9]{32})\s*$", event.get("last-assistant-message") or "")
    return match[1] if match else None


def completion(run):
    meta = read(run / "run.json")
    progress = read(run / "progress.json") if (run / "progress.json").exists() else {}
    # Forked reviewers inherit both notify and the original input. Bind only after
    # a matching run-owned report, never merely from the first matching prompt.
    # This also recovers old checkpoints incorrectly bound to a review child.
    bound_thread = progress.get("thread") if progress.get("reports") else None
    matches = {}
    for path in (run / "events").glob("*.json"):
        event = read(path)
        inputs = event.get("input-messages", [])
        if (event.get("type") == "agent-turn-complete"
                and event.get("cwd") == meta["repo"]
                and event.get("thread-id") and event.get("turn-id")
                and (event["thread-id"] == bound_thread
                     if bound_thread else inputs and inputs[0] == meta["prompt"])):
            marker = report_marker(event)
            if not bound_thread and (not marker or not (run / "reports" / (marker + ".json")).exists()):
                continue
            key = (event["thread-id"], event["turn-id"])
            payload = {k: v for k, v in event.items() if k != "received_ns"}
            if key in matches and {k: v for k, v in matches[key].items() if k != "received_ns"} != payload:
                raise ValueError("Conflicting completion events; inspect retained evidence")
            matches[key] = event
    if len({thread for thread, _ in matches}) > 1:
        raise ValueError("Multiple runtime conversations for this run; inspect before retry")
    matches = {key: event for key, event in matches.items() if list(key) not in progress.get("handled", [])}
    if not matches:
        return None
    key, event = min(matches.items(), key=lambda item: item[1].get("received_ns", 0))
    marker = report_marker(event)
    report = run / "reports" / (marker + ".json") if marker else None
    evidence = dict(event=list(key), report_id=marker)
    if not report or not report.exists() or marker in progress.get("reports", []):
        return dict(outcome="blocked", ticket=None,
                    summary="Turn ended without a fresh report marker. Attach and ask the agent to report again.", **evidence)
    result = dict(read(report), **evidence)
    if (result.get("run") != run.name or result.get("outcome") not in {"done", "retry", "blocked"}
            or not isinstance(result.get("summary"), str) or not result["summary"].strip()):
        raise ValueError("Invalid result report")
    number = result.get("ticket")
    if number is None:
        if result["outcome"] != "blocked":
            raise ValueError("Only blocked may omit a selected ticket")
        return result
    selected = meta["tickets"].get(number)
    if (not selected or selected["status"] != "ready-for-agent"
            or any(meta["tickets"][b]["status"] != "done" for b in selected["blockers"])):
        return dict(outcome="blocked", ticket=None,
                    summary=f"Reported ticket {number} was not eligible; inspect the agent's selection.", **evidence)
    if result["outcome"] == "done":
        checks = CHECK.findall(Path(selected["path"]).read_text())
        if [text for _, text in checks] != selected["criteria"] or any(x == " " for x, _ in checks):
            return dict(outcome="blocked", ticket=number,
                        summary="Done rejected: criteria changed or remain unchecked.", **evidence)
    return result


def prepare(repo, state, folder, attempts, timeout, instructions=DEFAULT_PROMPT):
    found = tickets(folder)
    run = state / "runs" / ("run-" + uuid.uuid4().hex[:12])
    (run / "events").mkdir(parents=True)
    report = shlex.join([sys.executable, str(SCRIPT), "_report", str(run)])
    prompt = f"""{instructions}

Ticket directory: {folder}
Choose exactly one ticket with Status ready-for-agent whose blockers are all done.
Read its dependencies and linked specification as needed. Do not work on other tickets.

Before your final response in each turn, report once using one of:
  {report} done <ticket-number> 'Summary and verification evidence'
  {report} retry <ticket-number> 'Progress made; concrete remaining implementation work'
  {report} blocked <ticket-number-or-dash> 'Exact missing access, decision, human action or evidence'
Report the two-digit number of the ticket you selected; use - if none can be selected.
For done, check off each satisfied original acceptance criterion and record its
verification evidence in the ticket. Report done only when all criteria are satisfied.
Before reporting done, commit the completed ticket's changes, including its checkbox
and verification-evidence updates, to the current branch. Commit only changes for
this ticket, preserve unrelated work, and include the commit hash in the done summary.
Use retry for work another fresh context can finish without human input.
Use blocked when human input, external access or required evidence is missing.
End your final response with the exact RALPH_REPORT marker printed by the command.
If the operator follows up in this session, report again with a fresh marker.
The runner updates ticket Status. After reporting, finish your turn.
"""
    write(run / "prompt.txt", prompt)
    snapshot = {n: dict(path=str(t["path"]), status=t["status"], blockers=t["blockers"],
                        criteria=[text for _, text in CHECK.findall(t["body"])])
                for n, t in found.items()}
    meta = dict(repo=str(repo), tickets=snapshot, prompt=prompt, attempts=dict(attempts),
                session=run.name, deadline=time.time() + timeout)
    write(run / "run.json", meta)
    return run, meta


def launch(repo, run, meta):
    tmux(repo, "new-session", "-d", "-s", meta["session"], "-c", str(repo),
         "-x", "160", "-y", "50", sys.executable, str(SCRIPT), "_worker", str(run))
    tmux(repo, "set-option", "-t", meta["session"], "remain-on-exit", "on")
    tmux(repo, "pipe-pane", "-o", "-t", meta["session"],
         "cat >> " + shlex.quote(str(run / "terminal.log")))
    write(run / "go", "go\n")


def run_loop(args, state):
    checkpoint = state / "state.json"
    data = read(checkpoint) if checkpoint.exists() else {"active": None, "attempts": {}}
    while True:
        if data["active"]:
            run = Path(data["active"])
            meta = read(run / "run.json")
            print(f"Agent selecting work: tmux -L {socket(args.repo)} attach -t {meta['session']}", flush=True)
            while True:
                result = completion(run)
                if result:
                    break
                progress = read(run / "progress.json") if (run / "progress.json").exists() else {}
                if progress.get("waiting") and (state / "STOP").exists():
                    print("Stopped watching; interactive session retained. Use start to resume watching.")
                    return 0
                if not exists(args.repo, meta["session"]) or (not progress.get("waiting") and time.time() > meta["deadline"]):
                    raise ValueError(f"Session lost or deadline reached. No relaunch. Inspect {run}; retry explicitly after stopping any surviving work.")
                dead = tmux(args.repo, "display-message", "-p", "-t", meta["session"], "#{pane_dead}").stdout.strip()
                if dead == "1":
                    raise ValueError(f"Codex exited without completion evidence. Inspect {run}; no automatic relaunch.")
                time.sleep(2)
            # Save outcome before effects, so restart repeats only idempotent finalization.
            write(run / "outcome.json", result)
            outcome = result["outcome"]
            number = result["ticket"]
            if number is not None:
                # Snapshot-based accounting keeps finalization idempotent after restart.
                data["attempts"][number] = meta["attempts"].get(number, 0) + 1
                if outcome == "retry" and data["attempts"][number] >= args.max_attempts:
                    outcome = "blocked"
                    result["summary"] += " Automatic attempt limit reached; use retry after inspection."
                mark(Path(meta["tickets"][number]["path"]), "ready-for-agent" if outcome == "retry" else outcome)
            print(f"{number or 'No ticket'}: {outcome}: {result['summary']}", flush=True)
            if outcome == "blocked":
                # Consume this turn, retaining its live conversation for operator follow-up.
                write(checkpoint, data)
                progress = read(run / "progress.json") if (run / "progress.json").exists() else {}
                progress.update(thread=result["event"][0], waiting=True, summary=result["summary"])
                progress.setdefault("handled", []).append(result["event"])
                if result["report_id"]:
                    progress.setdefault("reports", []).append(result["report_id"])
                write(run / "progress.json", progress)
                print(f"Waiting for your input: tmux -L {socket(args.repo)} attach -t {meta['session']}", flush=True)
                continue
            if exists(args.repo, meta["session"]):
                tmux(args.repo, "kill-session", "-t", "=" + meta["session"])
            data["active"] = None
            write(checkpoint, data)
        if (state / "STOP").exists():
            print("Stopped before starting another ticket. Remove STOP (or use start) to resume.")
            return 0
        found = tickets(args.tickets)
        if all(t["status"] == "done" for t in found.values()):
            print("All tickets done.")
            return 0
        run, meta = prepare(args.repo, state, args.tickets, data["attempts"], args.timeout, args.prompt)
        data["active"] = str(run)
        write(checkpoint, data)  # A crash here never causes automatic redispatch.
        launch(args.repo, run, meta)


def internal(argv):
    command, directory, *rest = argv
    run = Path(directory).resolve()
    if command == "_notify":
        event = json.loads(rest[-1])
        event["received_ns"] = time.time_ns()
        write(run / "events" / (uuid.uuid4().hex + ".json"), event)
    elif command == "_report":
        outcome, number, summary = rest
        if outcome not in {"done", "retry", "blocked"} or not summary.strip():
            raise ValueError("Expected done/retry/blocked and a nonempty summary")
        if number == "-":
            if outcome != "blocked":
                raise ValueError("Only blocked may omit a selected ticket")
            number = None
        elif number not in read(run / "run.json")["tickets"]:
            raise ValueError("Expected the selected ticket's two-digit number")
        report_id = uuid.uuid4().hex
        report = dict(run=run.name, outcome=outcome, ticket=number, summary=summary, report_id=report_id)
        write(run / "reports" / (report_id + ".json"), report)
        write(run / "result.json", report)
        print("RALPH_REPORT:" + report_id)
    elif command == "_worker":
        while not (run / "go").exists():
            time.sleep(0.1)
        meta = read(run / "run.json")
        notify = [sys.executable, str(SCRIPT), "_notify", str(run)]
        os.execvp("codex", ["codex", "--yolo", "--no-alt-screen", "-C", meta["repo"],
                           "-c", "notify=" + json.dumps(notify), "--", meta["prompt"]])
    else:
        raise ValueError(f"Unknown internal command: {command}")
    return 0


def main():
    if len(sys.argv) > 1 and sys.argv[1].startswith("_"):
        return internal(sys.argv[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["start", "run", "status", "stop", "retry"])
    parser.add_argument("ticket", nargs="?", help="Ticket number for retry")
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Target repository (default: current directory)")
    parser.add_argument("--tickets", type=Path, help="Ticket directory (default: <repo>/issues)")
    parser.add_argument("--state", type=Path, help="Runtime directory (default: <repo>/.ralph)")
    prompts = parser.add_mutually_exclusive_group()
    prompts.add_argument("--prompt", help="Task instructions; ticket context and reporting rules are appended")
    prompts.add_argument("--prompt-file", type=Path, help="Read task instructions from a UTF-8 file")
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=8 * 60 * 60, help="Seconds per attempt; timeout preserves the session")
    args = parser.parse_args()
    args.repo = args.repo.resolve()
    args.tickets = (args.tickets or args.repo / "issues").resolve()
    state = (args.state or args.repo / ".ralph").resolve()
    if args.timeout <= 0 or args.max_attempts < 1:
        parser.error("timeout and max-attempts must be positive")
    if args.prompt_file is not None:
        try:
            args.prompt = args.prompt_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            parser.error(f"Cannot read prompt file: {error}")
    if args.prompt is None:
        args.prompt = DEFAULT_PROMPT
    if not args.prompt.strip():
        parser.error("prompt must not be empty")
    if args.command == "status":
        found = tickets(args.tickets)
        ready = frontier(found)
        for number, ticket in found.items():
            print(f"{number}  {ticket['status']:15} {'runnable' if number in ready else ''}  {ticket['path'].stem[3:]}")
        if (state / "state.json").exists():
            active = read(state / "state.json")["active"]
            print("Active:", active or "none")
            if active:
                meta = read(Path(active) / "run.json")
                print("Ticket selection: delegated to the agent; recorded in its result")
                print("Worker attach:", f"tmux -L {socket(args.repo)} attach -t {meta['session']}")
                if (Path(active) / "progress.json").exists():
                    print("Waiting for input:", read(Path(active) / "progress.json")["summary"])
        print("Runner attach:", f"tmux -L {socket(args.repo)} attach -t loop")
        print("Logs:", state)
        return 0
    state.mkdir(parents=True, exist_ok=True)
    if args.command == "stop":
        write(state / "STOP", "stop after current attempt\n")
        print("Stop requested. Current attempt may finish; no next ticket will launch.")
        return 0
    # The same repo lock applies even with alternative ticket/state directories.
    lock = args.repo / ".ralph/runner.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a") as held:
        try:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("A Ralph runner is already active for this repository")
        if args.command == "retry":
            number = (args.ticket or "").zfill(2)
            found = tickets(args.tickets)
            if number not in found or found[number]["status"] == "done":
                raise ValueError("retry requires an unfinished ticket number")
            checkpoint = state / "state.json"
            data = read(checkpoint) if checkpoint.exists() else {"active": None, "attempts": {}}
            if data["active"]:
                run = Path(data["active"])
                meta = read(run / "run.json")
                selected = read(run / "result.json").get("ticket") if (run / "result.json").exists() else None
                if (selected is not None and selected != number) or exists(args.repo, meta["session"]):
                    raise ValueError("Stop and inspect the active worker before retry; no overlapping launch is allowed")
                data["active"] = None
            data["attempts"][number] = 0
            mark(found[number]["path"], "ready-for-agent")
            write(checkpoint, data)
            print(f"Ticket {number} requeued. Use start to resume.")
            return 0
        if args.command == "start":
            reuse = exists(args.repo, "loop")
            if reuse:
                dead = tmux(args.repo, "display-message", "-p", "-t", "loop", "#{pane_dead}").stdout.strip()
                if dead != "1":
                    raise ValueError("Runner session already exists; attach or use status")
            (state / "STOP").unlink(missing_ok=True)
            # Start after this process releases its lock; run obtains the same lock.
            command = shlex.join([sys.executable, str(SCRIPT), "run", "--repo", str(args.repo),
                                 "--tickets", str(args.tickets), "--state", str(state),
                                 "--max-attempts", str(args.max_attempts), "--timeout", str(args.timeout),
                                 "--prompt=" + args.prompt])
            shell = "sleep 1; " + command + " >> " + shlex.quote(str(state / "loop.log")) + " 2>&1"
            if reuse:
                tmux(args.repo, "respawn-pane", "-k", "-t", "loop", "-c", str(args.repo), shell)
            else:
                tmux(args.repo, "new-session", "-d", "-s", "loop", "-c", str(args.repo), shell)
            tmux(args.repo, "set-option", "-t", "loop", "remain-on-exit", "on")
            print("Started. Use status for progress; tail -f", shlex.quote(str(state / "loop.log")))
            return 0
        return run_loop(args, state)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"ralph: {error}", file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        print("Runner interrupted; active tmux worker preserved. Run again to watch it.")
        sys.exit(130)
