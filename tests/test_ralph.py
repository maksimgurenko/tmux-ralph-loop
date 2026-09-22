import importlib.util
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import contextlib
import io
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/ralph.py"
spec = importlib.util.spec_from_file_location("ralph", SCRIPT)
ralph = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ralph)


class RalphTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ralph-test-")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.folder = self.repo / "issues"
        self.folder.mkdir()
        self.state = self.repo / "state"

    def ticket(self, n="01", blocker=None, status="ready-for-agent"):
        edge = f"[{blocker}: Test]({blocker}-test.md)" if blocker else "None (can start immediately)"
        path = self.folder / f"{n}-test.md"
        path.write_text(f"# {n}: Test\n\n**Blocked by:** {edge}.\n\n**Status:** {status}\n\n- [ ] Verify behavior\n")
        return path

    def assignment(self):
        self.ticket()
        return ralph.prepare(self.repo, self.state, self.folder, {}, 30)

    def event(self, run, meta, **changes):
        event = {"type": "agent-turn-complete", "cwd": str(self.repo), "input-messages": [meta["prompt"]],
                 "thread-id": "thread", "turn-id": "turn", "received_ns": time.time_ns()}
        if (run / "result.json").exists():
            event["last-assistant-message"] = "RALPH_REPORT:" + ralph.read(run / "result.json")["report_id"]
        event.update(changes)
        ralph.write(run / "events" / f"event-{len(list((run / 'events').glob('*.json')))}.json", event)

    def report(self, run, outcome="done", number="01"):
        with contextlib.redirect_stdout(io.StringIO()):
            ralph.internal(["_report", str(run), outcome, number, "Evidence recorded"])

    def test_dependency_frontier_and_checked_done(self):
        first = self.ticket()
        self.ticket("02", "01")
        self.assertEqual(ralph.frontier(ralph.tickets(self.folder)), ["01"])
        ralph.mark(first, "done")
        with self.assertRaises(ValueError):
            ralph.tickets(self.folder)
        first.write_text(first.read_text().replace("[ ]", "[x]"))
        self.assertEqual(ralph.frontier(ralph.tickets(self.folder)), ["02"])

    def test_invalid_graph_fails_closed(self):
        self.ticket(blocker="02")
        with self.assertRaises(ValueError):
            ralph.tickets(self.folder)

    def test_report_alone_and_title_callback_do_not_finish(self):
        run, meta = self.assignment()
        self.report(run)
        self.assertIsNone(ralph.completion(run))
        self.event(run, meta, **{"input-messages": ["Generate a title for " + meta["prompt"]]})
        self.assertIsNone(ralph.completion(run))
        self.event(run, meta, **{"input-messages": [meta["prompt"], "manual turn"], "last-assistant-message": "No report"})
        self.assertIsNone(ralph.completion(run))

    def test_completion_requires_original_checked_criteria(self):
        run, meta = self.assignment()
        self.report(run)
        self.event(run, meta)
        self.assertEqual(ralph.completion(run)["outcome"], "blocked")
        ticket = Path(meta["tickets"]["01"]["path"])
        ticket.write_text(ticket.read_text().replace("[ ]", "[x]"))
        self.assertEqual(ralph.completion(run)["outcome"], "done")
        ticket.write_text(ticket.read_text().replace("Verify behavior", "Do something easier"))
        self.assertEqual(ralph.completion(run)["outcome"], "blocked")

    def test_missing_report_blocks_and_conflicting_events_fail(self):
        run, meta = self.assignment()
        self.report(run, "blocked")
        old_id = ralph.read(run / "result.json")["report_id"]
        ralph.write(run / "progress.json", {"thread": "thread", "handled": [], "reports": [old_id]})
        self.event(run, meta, **{"last-assistant-message": "No report"})
        self.assertEqual(ralph.completion(run)["outcome"], "blocked")
        self.event(run, meta, **{"last-assistant-message": "different"})
        with self.assertRaises(ValueError):
            ralph.completion(run)

    def test_inherited_review_callbacks_cannot_bind_and_old_bad_binding_recovers(self):
        run, meta = self.assignment()
        self.event(run, meta, **{"thread-id": "standards-review", "last-assistant-message": "One standards finding"})
        self.event(run, meta, **{"thread-id": "spec-review", "last-assistant-message": "One spec finding"})
        self.assertIsNone(ralph.completion(run))
        # Reproduce the faulty checkpoint saved by the original runner.
        ralph.write(run / "progress.json", {"thread": "standards-review", "waiting": True,
                                            "handled": [["standards-review", "turn"]]})
        ticket = Path(meta["tickets"]["01"]["path"])
        ticket.write_text(ticket.read_text().replace("[ ]", "[x]"))
        self.report(run)
        self.event(run, meta, **{"thread-id": "main-implementer"})
        result = ralph.completion(run)
        self.assertEqual(result["outcome"], "done")
        self.assertEqual(result["ticket"], "01")
        self.assertEqual(result["event"], ["main-implementer", "turn"])

    def test_followup_reports_preserve_previous_reports(self):
        run, _ = self.assignment()
        self.report(run, "blocked")
        first = ralph.read(run / "result.json")
        self.report(run)
        self.assertNotEqual(first["report_id"], ralph.read(run / "result.json")["report_id"])
        self.assertEqual(ralph.read(run / "reports" / (first["report_id"] + ".json")), first)

    def test_agent_cannot_complete_a_ticket_with_unfinished_blockers(self):
        self.ticket()
        self.ticket("02", "01")
        run, meta = ralph.prepare(self.repo, self.state, self.folder, {}, 30)
        self.report(run, number="02")
        self.event(run, meta)
        result = ralph.completion(run)
        self.assertEqual(result["outcome"], "blocked")
        self.assertIsNone(result["ticket"])

    def test_agent_can_report_no_available_work(self):
        run, meta = self.assignment()
        self.report(run, "blocked", "-")
        self.event(run, meta)
        self.assertEqual(ralph.completion(run)["outcome"], "blocked")
        self.assertIsNone(ralph.completion(run)["ticket"])

    def test_unknown_ticket_report_is_rejected(self):
        run, _ = self.assignment()
        with self.assertRaises(ValueError):
            self.report(run, number="99")

    def test_unreported_selection_remains_stopped_across_restart(self):
        run, meta = self.assignment()
        self.event(run, meta)
        checkpoint = self.state / "state.json"
        ralph.write(checkpoint, {"active": str(run), "attempts": {}})
        args = ralph.argparse.Namespace(repo=self.repo, tickets=self.folder, max_attempts=3, timeout=30,
                                       prompt=ralph.DEFAULT_PROMPT)
        with patch.object(ralph, "exists", return_value=False), patch.object(ralph, "launch") as launch:
            with self.assertRaisesRegex(ValueError, "No relaunch"):
                ralph.run_loop(args, self.state)
            with self.assertRaisesRegex(ValueError, "No relaunch"):
                ralph.run_loop(args, self.state)
            launch.assert_not_called()
        self.assertEqual(ralph.read(checkpoint)["active"], str(run))
        self.assertEqual(ralph.tickets(self.folder)["01"]["status"], "ready-for-agent")

    def test_followup_waits_for_its_own_report_and_runtime_event(self):
        run, meta = self.assignment()
        self.report(run, "blocked")
        old_id = ralph.read(run / "result.json")["report_id"]
        self.event(run, meta)
        ralph.write(run / "progress.json", {"thread": "thread", "handled": [["thread", "turn"]],
                                            "reports": [old_id], "waiting": True})
        ticket = Path(meta["tickets"]["01"]["path"])
        ticket.write_text(ticket.read_text().replace("[ ]", "[x]"))
        self.report(run)
        self.assertIsNone(ralph.completion(run))
        self.event(run, meta, **{"turn-id": "human", "input-messages": [meta["prompt"], "I fixed it; continue"]})
        self.assertEqual(ralph.completion(run)["outcome"], "done")
        self.assertEqual(ralph.completion(run)["event"], ["thread", "human"])

    def test_old_report_marker_cannot_complete_a_followup(self):
        run, meta = self.assignment()
        self.report(run, "blocked")
        old_id = ralph.read(run / "result.json")["report_id"]
        self.event(run, meta)
        ralph.write(run / "progress.json", {"thread": "thread", "handled": [["thread", "turn"]], "reports": [old_id]})
        self.event(run, meta, **{"turn-id": "two", "input-messages": [meta["prompt"], "hello"]})
        self.assertEqual(ralph.completion(run)["outcome"], "blocked")
        self.assertIn("fresh report", ralph.completion(run)["summary"])

    def test_restart_consumes_followup_without_counting_another_attempt(self):
        run, meta = self.assignment()
        self.report(run, "blocked")
        old_id = ralph.read(run / "result.json")["report_id"]
        self.event(run, meta)
        ralph.write(run / "progress.json", {"thread": "thread", "handled": [["thread", "turn"]],
                                            "reports": [old_id], "waiting": True})
        checkpoint = self.state / "state.json"
        ralph.write(checkpoint, {"active": str(run), "attempts": {"01": 1}})
        ticket = Path(meta["tickets"]["01"]["path"])
        ralph.mark(ticket, "blocked")
        ticket.write_text(ticket.read_text().replace("[ ]", "[x]"))
        self.report(run)
        self.event(run, meta, **{"turn-id": "two", "input-messages": [meta["prompt"], "continue"]})
        args = ralph.argparse.Namespace(repo=self.repo, tickets=self.folder, max_attempts=3, timeout=30,
                                       prompt=ralph.DEFAULT_PROMPT)
        with patch.object(ralph, "exists", return_value=False), patch.object(ralph, "launch") as launch:
            self.assertEqual(ralph.run_loop(args, self.state), 0)
            launch.assert_not_called()
        self.assertEqual(ralph.read(checkpoint), {"active": None, "attempts": {"01": 1}})

    def test_stop_prevents_initial_dispatch(self):
        self.ticket()
        self.fake_codex("done")
        ralph.write(self.state / "STOP", "stop\n")
        result = self.invoke("run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.state / "runs").exists())
        self.assertEqual(ralph.tickets(self.folder)["01"]["status"], "ready-for-agent")

    def test_invalid_prompt_overrides_do_not_create_runtime_state(self):
        empty = self.repo / "empty.txt"
        empty.write_text(" \n")
        invalid = self.repo / "invalid.txt"
        invalid.write_bytes(b"\xff")
        cases = [
            ("--prompt", ""),
            ("--prompt", " \n"),
            ("--prompt-file", str(empty)),
            ("--prompt-file", str(invalid)),
            ("--prompt-file", str(self.repo / "missing.txt")),
            ("--prompt", "instructions", "--prompt-file", str(empty)),
        ]
        for args in cases:
            with self.subTest(args=args):
                result = self.cli("start", *args, cwd=self.repo)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertFalse((self.repo / ".ralph").exists())

    @unittest.skipUnless(shutil.which("tmux"), "requires tmux")
    def test_default_paths_use_working_project_and_builtin_prompt(self):
        self.ticket()
        self.fake_codex("done")
        result = self.cli("run", "--timeout", "20", cwd=self.repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        state = self.repo / ".ralph"
        self.assertTrue((state / "runner.lock").exists())
        run, = (state / "runs").iterdir()
        meta = ralph.read(run / "run.json")
        self.assertEqual(meta["repo"], str(self.repo))
        self.assertTrue(meta["prompt"].startswith(ralph.DEFAULT_PROMPT))
        self.assertNotIn("$implement", meta["prompt"])
        self.assertEqual(ralph.tickets(self.folder)["01"]["status"], "done")
        status = self.cli("status", cwd=self.repo)
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIn("tmux-ralph-loop-", status.stdout)
        self.assertIn(str(state), status.stdout)

    @unittest.skipUnless(shutil.which("tmux"), "requires tmux")
    def test_detached_start_preserves_inline_and_file_prompts(self):
        self.fake_codex("done")
        marker = self.repo / "unexpected-shell-expansion"
        instructions = (f"-$implement Use 'single' and \"double\" quotes.\n"
                        f"Keep $HOME, {{literal}}, $(touch {marker}) and `touch {marker}` literal.")
        for source in ("inline", "file"):
            with self.subTest(source=source):
                self.ticket()
                state = self.repo / (source + " runtime")
                prompt_file = self.repo / "task instructions.txt"
                prompt_file.write_text(instructions, encoding="utf-8")
                args = (["--prompt=" + instructions] if source == "inline"
                        else ["--prompt-file", str(prompt_file)])
                result = self.cli("start", "--repo", str(self.repo), "--state", str(state),
                                  "--timeout", "20", *args)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                # start must forward the content, not defer reading the file to its child.
                prompt_file.write_text("Changed after startup")
                log = state / "loop.log"
                self.wait_for(lambda: log.exists() and "All tickets done." in log.read_text())
                self.wait_for(lambda: ralph.tmux(self.repo, "display-message", "-p", "-t", "loop",
                                                "#{pane_dead}").stdout.strip() == "1")
                run, = (state / "runs").iterdir()
                meta = ralph.read(run / "run.json")
                self.assertTrue(meta["prompt"].startswith(instructions + "\n\n"))
                self.assertIn("Ticket directory: " + str(self.folder), meta["prompt"])
                self.assertIn("Status ready-for-agent whose blockers are all done", meta["prompt"])
                self.assertIn("_report", meta["prompt"])
                self.assertIn("RALPH_REPORT", meta["prompt"])
                self.assertEqual((run / "prompt.txt").read_text(), meta["prompt"])
                self.assertEqual(ralph.tickets(self.folder)["01"]["status"], "done")
                self.assertFalse(marker.exists())

    def wait_for(self, condition):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.05)
        self.fail("Timed out waiting for runner progress")

    def test_second_runner_cannot_dispatch(self):
        self.ticket()
        self.fake_codex("done")
        lock = self.repo / ".ralph/runner.lock"
        lock.parent.mkdir(parents=True)
        with lock.open("a") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for state in (self.state, self.repo / "alternative-state"):
                with self.subTest(state=state):
                    result = self.cli("run", "--repo", str(self.repo), "--state", str(state))
                    self.assertEqual(result.returncode, 2)
                    self.assertIn("already active", result.stderr)
                    self.assertFalse((state / "runs").exists())

    @unittest.skipUnless(shutil.which("tmux"), "requires tmux")
    def test_uncertain_dispatch_is_not_relaunched_after_restart(self):
        run, _ = self.assignment()
        self.fake_codex("done")
        ralph.write(self.state / "state.json", {"active": str(run), "attempts": {"01": 1}})
        result = self.invoke("run")
        self.assertEqual(result.returncode, 2)
        self.assertIn("No relaunch", result.stderr)
        self.assertEqual(ralph.read(self.state / "state.json")["active"], str(run))
        self.assertEqual(len(list((self.state / "runs").iterdir())), 1)

    @unittest.skipUnless(shutil.which("tmux"), "requires tmux")
    def test_real_tmux_runs_two_tickets_with_simulated_codex(self):
        self.ticket()
        self.ticket("02", "01")
        self.fake_codex("done")
        result = self.invoke("run")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("All tickets done", result.stdout)
        self.assertEqual([t["status"] for t in ralph.tickets(self.folder).values()], ["done", "done"])
        self.assertEqual(len(list((self.state / "runs").iterdir())), 2)

    @unittest.skipUnless(shutil.which("tmux"), "requires tmux")
    def test_agent_can_choose_higher_numbered_eligible_ticket_first(self):
        self.ticket()
        self.ticket("02")
        self.fake_codex("done")
        result = self.invoke("run")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertLess(result.stdout.index("02: done:"), result.stdout.index("01: done:"))
        self.assertEqual(ralph.read(self.state / "state.json")["attempts"], {"02": 1, "01": 1})

    @unittest.skipUnless(shutil.which("tmux"), "requires tmux")
    def test_retry_budget_stops_before_next_ticket(self):
        self.ticket()
        self.ticket("02", "01")
        self.fake_codex("retry")
        result = self.invoke("run", "--max-attempts", "2")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        found = ralph.tickets(self.folder)
        self.assertEqual(found["01"]["status"], "blocked")
        self.assertEqual(found["02"]["status"], "ready-for-agent")
        self.assertEqual(len(list((self.state / "runs").iterdir())), 2)

    @unittest.skipUnless(shutil.which("tmux"), "requires tmux")
    def test_blocked_session_accepts_typed_followup_and_finishes_same_run(self):
        self.ticket()
        self.fake_codex("interactive")
        env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ["PATH"])
        command = [sys.executable, str(SCRIPT), "run", "--repo", str(self.repo),
                   "--tickets", str(self.folder), "--state", str(self.state), "--timeout", "20"]
        with subprocess.Popen(command, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
            try:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    progress = list((self.state / "runs").glob("*/progress.json"))
                    if progress:
                        break
                    time.sleep(0.05)
                self.assertTrue(progress, "runner never entered interactive waiting")
                run = progress[0].parent
                meta = ralph.read(run / "run.json")
                self.assertIsNone(proc.poll())
                self.assertTrue(ralph.exists(self.repo, meta["session"]))
                self.assertEqual(ralph.tickets(self.folder)["01"]["status"], "blocked")
                ralph.tmux(self.repo, "send-keys", "-t", meta["session"], "-l", "I fixed it; continue")
                ralph.tmux(self.repo, "send-keys", "-t", meta["session"], "Enter")
                stdout, stderr = proc.communicate(timeout=15)
                self.assertEqual(proc.returncode, 0, stdout + stderr)
                self.assertEqual(ralph.tickets(self.folder)["01"]["status"], "done")
                self.assertEqual(len(list((self.state / "runs").iterdir())), 1)
                self.assertEqual(len(list((run / "reports").glob("*.json"))), 2)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()

    @unittest.skipUnless(shutil.which("tmux"), "requires tmux")
    def test_detached_resume_retains_original_prompt_and_conversation(self):
        self.ticket()
        self.fake_codex("interactive")
        result = self.invoke("start", "--prompt", "$implement Original instructions")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.wait_for(lambda: bool(list((self.state / "runs").glob("*/progress.json"))))
        run, = (self.state / "runs").iterdir()
        meta = ralph.read(run / "run.json")
        original_prompt = (run / "prompt.txt").read_text()
        result = self.invoke("stop")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.wait_for(lambda: ralph.tmux(self.repo, "display-message", "-p", "-t", "loop",
                                        "#{pane_dead}").stdout.strip() == "1")
        self.assertTrue(ralph.exists(self.repo, meta["session"]))
        result = self.invoke("start", "--prompt", "New instructions for future attempts")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        ralph.tmux(self.repo, "send-keys", "-t", meta["session"], "-l", "I fixed it; continue")
        ralph.tmux(self.repo, "send-keys", "-t", meta["session"], "Enter")
        self.wait_for(lambda: "All tickets done." in (self.state / "loop.log").read_text())
        self.assertEqual((run / "prompt.txt").read_text(), original_prompt)
        self.assertEqual(len(list((self.state / "runs").iterdir())), 1)
        self.assertEqual(len(list((run / "reports").glob("*.json"))), 2)
        self.assertEqual(ralph.tickets(self.folder)["01"]["status"], "done")

    def fake_codex(self, outcome):
        self.bin = self.repo / "bin"
        self.bin.mkdir()
        fake = self.bin / "codex"
        fake.write_text(f'''#!{sys.executable}
import argparse, json, pathlib, subprocess, sys
parser = argparse.ArgumentParser()
parser.add_argument('--yolo', action='store_true')
parser.add_argument('--no-alt-screen', action='store_true')
parser.add_argument('-C')
parser.add_argument('-c')
parser.add_argument('prompt')
args = parser.parse_args()
notify = json.loads(args.c.removeprefix('notify='))
run = pathlib.Path(notify[-1])
meta = json.loads((run / 'run.json').read_text())
assert args.prompt == meta['prompt'], 'Prompt changed during launch'
# Simulate an agent selecting independently, deliberately preferring higher IDs.
eligible = [n for n, t in meta['tickets'].items() if t['status'] == 'ready-for-agent'
            and all(meta['tickets'][b]['status'] == 'done' for b in t['blockers'])]
number = sorted(eligible, reverse=True)[0]
ticket = pathlib.Path(meta['tickets'][number]['path'])
outcome = {outcome!r}
if outcome == 'done':
    ticket.write_text(ticket.read_text().replace('[ ]', '[x]'))
reported = subprocess.run([sys.executable, notify[1], '_report', str(run), 'blocked' if outcome == 'interactive' else outcome, number, 'Simulated model result'], check=True, capture_output=True, text=True)
event = {{'type': 'agent-turn-complete', 'cwd': meta['repo'], 'input-messages': [meta['prompt']], 'thread-id': run.name, 'turn-id': 'one', 'last-assistant-message': reported.stdout.strip()}}
subprocess.run(notify + [json.dumps(event)], check=True)
if outcome == 'interactive':
    followup = input('Blocked. Supply instructions: ')
    ticket.write_text(ticket.read_text().replace('[ ]', '[x]'))
    reported = subprocess.run([sys.executable, notify[1], '_report', str(run), 'done', number, 'Fixed after human input'], check=True, capture_output=True, text=True)
    event.update({{'turn-id': 'two', 'input-messages': [meta['prompt'], followup], 'last-assistant-message': reported.stdout.strip()}})
    subprocess.run(notify + [json.dumps(event)], check=True)
''')
        fake.chmod(0o755)
        self.addCleanup(lambda: ralph.tmux(self.repo, "kill-server", check=False))

    def invoke(self, *commands):
        return self.cli(*commands, "--repo", str(self.repo), "--tickets", str(self.folder),
                        "--state", str(self.state), "--timeout", "20")

    def cli(self, *args, cwd=None):
        env = dict(os.environ, PATH=str(self.repo / "bin") + os.pathsep + os.environ["PATH"])
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=cwd,
                              env=env, text=True, capture_output=True, timeout=30)


if __name__ == "__main__":
    unittest.main()
