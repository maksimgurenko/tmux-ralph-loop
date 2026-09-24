"""Opt-in end-to-end tests using authenticated CLIs and real model calls.

RALPH_LIVE_TESTS=1 python3 -m unittest discover -s tests -p test_live_agents.py -v
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from test_ralph import SCRIPT, ralph


FIXTURE = Path(__file__).parent / "fixtures/live"
PROMPT = """This is a tiny local smoke-test repository. Take the next eligible task
from the ticket directory and complete exactly that task. Use the existing CLI
tools directly; no skills, delegation, network research, or additional features
are needed. Verify the exact output bytes with Python, record evidence, commit,
and follow the appended reporting instructions. Do not change acceptance text.
Do not push or contact external services other than your configured model."""


@unittest.skipUnless(os.environ.get("RALPH_LIVE_TESTS") == "1",
                     "opt in with RALPH_LIVE_TESTS=1 (uses real model calls)")
class LiveAgentTests(unittest.TestCase):
    def command(self, *args, text=True):
        result = subprocess.run(args, cwd=self.repo, text=text, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def exercise(self, agent):
        for executable in ("git", "tmux", agent):
            self.assertIsNotNone(shutil.which(executable), f"Missing executable: {executable}")
        timeout = float(os.environ.get("RALPH_LIVE_TIMEOUT", "300"))
        self.assertGreater(timeout, 0)
        root = Path(os.environ.get("RALPH_LIVE_ARTIFACTS",
                                  str(SCRIPT.parent.parent / ".ralph/live-tests"))).resolve()
        root.mkdir(parents=True, exist_ok=True)
        artifacts = Path(tempfile.mkdtemp(prefix=agent + "-", dir=root))
        self.repo = artifacts / "repo"
        shutil.copytree(FIXTURE, self.repo)
        self.state = self.repo / ".ralph"
        print(f"\n{agent}: retained artifacts: {artifacts}", flush=True)
        # Each fresh repository has its own Ralph tmux socket. Never touch the
        # user's default server or another test's sessions, even on failure.
        self.addCleanup(self.cleanup, artifacts)
        self.command("git", "init", "-q", "-b", "smoke-test")
        for key, value in (("user.name", "Ralph smoke test"),
                           ("user.email", "ralph-test@example.invalid"),
                           ("commit.gpgsign", "false"),
                           ("core.hooksPath", str(self.repo / ".git/no-hooks"))):
            self.command("git", "config", "--local", key, value)
        (self.repo / ".gitignore").write_text(".ralph/\n")
        self.command("git", "add", ".")
        self.command("git", "commit", "-qm", "Seed live smoke-test tasks")
        for executable in ("tmux", agent):
            (artifacts / f"{executable}-version.txt").write_text(
                self.command(executable, "-V" if executable == "tmux" else "--version"))
        args = [sys.executable, str(SCRIPT), "start", "--repo", str(self.repo),
                "--agent", agent, "--timeout", str(timeout), "--max-attempts", "1",
                "--prompt", PROMPT]
        (artifacts / "start.txt").write_text(self.command(*args))
        observed = {}
        trust_key_at = {}
        deadline = time.monotonic() + 2 * timeout + 30
        while time.monotonic() < deadline:
            panes = ralph.tmux(self.repo, "list-panes", "-a", "-F",
                              "#{session_name}\t#{pane_dead}\t#{pane_current_path}\t"
                              "#{pane_current_command}\t#{pane_pid}\t#{pane_dead_status}",
                              check=False)
            self.assertEqual(panes.returncode, 0, panes.stderr)
            current = {}
            changed = False
            for line in panes.stdout.splitlines():
                name, dead, cwd, command, pid, status = line.split("\t")
                current[name] = dict(dead=dead, cwd=cwd, command=command, pid=pid, status=status)
                if dead == "0" and cwd == str(self.repo):
                    changed = changed or observed.get(name) != current[name]
                    observed[name] = current[name]
            if changed:
                ralph.write(artifacts / "observed-panes.json", observed)
            for run in (self.state / "runs").glob("*"):
                if agent in {"claude", "codex"} and run.name in current:
                    screen = ralph.tmux(self.repo, "capture-pane", "-p", "-t", run.name,
                                        check=False).stdout
                    # Only accept a recognized trust dialog naming the repo we
                    # just generated, never a general permission/setup prompt.
                    keys = None
                    if str(self.repo) in screen:
                        if (agent == "claude" and "Accessing workspace:" in screen
                                and "Yes, I trust this folder" in screen):
                            if "❯ No, exit" in screen:
                                keys = ("Down",)
                            elif "❯ Yes, I trust this folder" in screen:
                                keys = ("Enter",)
                        elif (agent == "codex" and "Trust this folder?" in screen
                              and "› 1. Trust and continue" in screen):
                            keys = ("Enter",)
                    # TUIs may render before attaching their key handlers. Space
                    # out keys and re-read the selected option before confirming.
                    if keys and time.monotonic() >= trust_key_at.setdefault(run.name, time.monotonic() + 1):
                        (artifacts / f"{run.name}-trust.txt").write_text(screen)
                        ralph.tmux(self.repo, "send-keys", "-t", run.name, *keys)
                        trust_key_at[run.name] = time.monotonic() + 1
                progress = run / "progress.json"
                if progress.exists():
                    self.assertFalse(ralph.read(progress).get("waiting"),
                                     f"{agent} blocked: {ralph.read(progress).get('summary')}")
            runner = current.get("loop")
            self.assertIsNotNone(runner, "Detached loop session disappeared")
            if runner["dead"] == "1":
                log = (self.state / "loop.log").read_text()
                self.assertEqual(runner["status"], "0", log)
                self.assertIn("All tickets done.", log)
                break
            time.sleep(0.1)
        else:
            self.fail(f"{agent} exceeded {2 * timeout + 30:g}s; inspect {artifacts}")

        self.assertIn("loop", observed, "Never observed a live detached watcher")
        checkpoint = ralph.read(self.state / "state.json")
        self.assertIsNone(checkpoint["active"])
        self.assertEqual(checkpoint["agent"], agent)
        self.assertEqual(checkpoint["attempts"], {"01": 1, "02": 1})
        found = ralph.tickets(self.repo / "issues")
        self.assertEqual({n: t["status"] for n, t in found.items()}, {"01": "done", "02": "done"})
        for path, expected in (("results/hello.txt", b"hello\n"), ("results/sum.txt", b"3\n")):
            self.assertEqual((self.repo / path).read_bytes(), expected)
            self.assertEqual(self.command("git", "show", "HEAD:" + path, text=False), expected)
        runs = list((self.state / "runs").iterdir())
        self.assertEqual(len(runs), 2, "Expected a fresh agent session for each task")
        conversations = set()
        completed = set()
        for run in runs:
            meta = ralph.read(run / "run.json")
            outcome = ralph.read(run / "outcome.json")
            self.assertEqual(meta["agent"], agent)
            self.assertIn(meta["session"], observed, "Never observed the worker alive in the fixture repo")
            self.assertFalse(ralph.exists(self.repo, meta["session"]), "Completed worker was not closed")
            self.assertGreater((run / "terminal.log").stat().st_size, 0)
            self.assertEqual(outcome["outcome"], "done")
            self.assertEqual(ralph.completion(run), outcome,
                             "Missing matching native completion event and report")
            conversations.add(outcome["event"][0])
            completed.add(outcome["ticket"])
            if outcome["ticket"] == "02":
                self.assertEqual(meta["tickets"]["01"]["status"], "done")
        self.assertEqual(completed, {"01", "02"})
        self.assertEqual(len(conversations), 2, "Tasks reused the same conversation")
        (artifacts / "status.txt").write_text(self.command(
            sys.executable, str(SCRIPT), "status", "--repo", str(self.repo)))
        print(f"{agent}: PASS (two tasks, native callbacks, live tmux sessions, committed outputs)", flush=True)

    def cleanup(self, artifacts):
        # Capture the final visible screen before stopping only this test's server.
        try:
            panes = ralph.tmux(self.repo, "list-panes", "-a", "-F", "#{pane_id}", check=False)
            for pane in panes.stdout.splitlines():
                result = ralph.tmux(self.repo, "capture-pane", "-p", "-S", "-200", "-t", pane, check=False)
                (artifacts / f"pane-{pane.lstrip('%')}.txt").write_text(result.stdout + result.stderr)
        finally:
            ralph.tmux(self.repo, "kill-server", check=False)

    def test_codex(self):
        self.exercise("codex")

    def test_claude(self):
        self.exercise("claude")

    def test_opencode(self):
        self.exercise("opencode")

    def test_pi(self):
        self.exercise("pi")


if __name__ == "__main__":
    unittest.main()
