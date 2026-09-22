import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_ralph

ralph = test_ralph.ralph
SCRIPT = test_ralph.SCRIPT
FIXTURES = Path(__file__).parent / "fixtures"


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.h = test_ralph.RalphTests()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.repo, self.state = self.h.repo, self.h.state

    def prepare(self, agent):
        self.h.ticket()
        return ralph.prepare(self.repo, self.state, self.h.folder, {}, 30, agent=agent)

    def hook(self, run, meta, kind, **fields):
        event = dict(hook_event_name=kind, session_id=meta["runtime_session_id"],
                     cwd=str(self.repo), **fields)
        result = subprocess.run([sys.executable, str(SCRIPT), "_claude_hook", str(run)],
                                input=json.dumps(event), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_claude_requires_original_prompt_main_session_and_stop(self):
        run, meta = self.prepare("claude")
        self.h.report(run, "blocked")
        marker = "RALPH_REPORT:" + ralph.read(run / "result.json")["report_id"]
        self.hook(run, meta, "Stop", last_assistant_message=marker)
        self.hook(run, meta, "UserPromptSubmit", prompt="Other work")
        self.hook(run, meta, "Stop", last_assistant_message=marker)
        self.assertIsNone(ralph.completion(run))
        self.hook(run, meta, "UserPromptSubmit", prompt=meta["prompt"])
        self.hook(run, meta, "SubagentStop", last_assistant_message=marker)
        self.hook(run, meta, "StopFailure", last_assistant_message=marker)
        self.hook(run, meta, "Stop", agent_id="child", last_assistant_message=marker)
        wrong_session = dict(meta, runtime_session_id="unrelated-session")
        self.hook(run, wrong_session, "Stop", last_assistant_message=marker)
        self.assertIsNone(ralph.completion(run))
        self.hook(run, meta, "Stop", last_assistant_message=marker)
        result = ralph.completion(run)
        self.assertEqual(result["outcome"], "blocked")
        self.assertEqual(result["event"][0], meta["runtime_session_id"])
        # Duplicate hook delivery produces the same logical event.
        self.hook(run, meta, "Stop", last_assistant_message=marker)
        self.assertEqual(ralph.completion(run), result)

    def test_claude_followup_requires_fresh_report(self):
        run, meta = self.prepare("claude")
        self.h.report(run, "blocked")
        marker = "RALPH_REPORT:" + ralph.read(run / "result.json")["report_id"]
        self.hook(run, meta, "UserPromptSubmit", prompt=meta["prompt"])
        self.hook(run, meta, "Stop", last_assistant_message=marker)
        result = ralph.completion(run)
        ralph.write(run / "progress.json", {"thread": result["event"][0], "handled": [result["event"]],
                                            "reports": [result["report_id"]], "waiting": True})
        self.hook(run, meta, "UserPromptSubmit", prompt="Continue")
        self.hook(run, meta, "Stop", last_assistant_message=marker)
        self.assertIn("fresh report", ralph.completion(run)["summary"])

    def test_other_backend_event_cannot_complete_codex_run(self):
        run, meta = self.prepare("codex")
        self.h.report(run, "blocked")
        self.h.event(run, meta, agent="pi")
        self.assertIsNone(ralph.completion(run))

    def test_active_agent_cannot_be_changed_and_status_shows_agent(self):
        run, meta = self.prepare("claude")
        ralph.write(self.state / "state.json", {"active": str(run), "attempts": {}, "agent": "claude"})
        result = self.h.invoke("start", "--agent", "pi")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Active run uses claude", result.stderr)
        self.assertEqual(ralph.read(self.state / "state.json")["active"], str(run))
        status = self.h.invoke("status")
        self.assertIn("Agent: claude", status.stdout)

    def test_missing_agent_fails_before_assignment(self):
        self.h.ticket()
        with patch.object(ralph.shutil, "which", return_value=None), patch.object(
                sys, "argv", [str(SCRIPT), "start", "--repo", str(self.repo), "--agent", "pi"]):
            with self.assertRaisesRegex(ValueError, "not found on PATH: pi"):
                ralph.main()
        self.assertFalse((self.repo / ".ralph/runs").exists())

    def test_launch_settings_are_per_process_and_keep_existing_opencode_plugins(self):
        run, meta = self.prepare("opencode")
        original = json.dumps({"model": "test/model", "plugin": ["existing-plugin"], "permission": "ask"})
        with patch.dict(os.environ, {"OPENCODE_CONFIG_CONTENT": original}):
            command, env = ralph.agent_command(run, meta)
            self.assertEqual(os.environ["OPENCODE_CONFIG_CONTENT"], original)
        config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        self.assertEqual(config["plugin"][0], "existing-plugin")
        self.assertEqual(config["model"], "test/model")
        self.assertEqual(config["permission"], "ask")
        self.assertIn("--auto", command)
        self.assertNotIn("run", command)
        self.assertFalse((self.repo / "opencode.json").exists())

    def test_legacy_pi_fails_with_upgrade_instruction(self):
        with patch.object(ralph.shutil, "which", return_value="/test/pi"), patch.object(
                ralph.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "0.73.1\n", "")):
            with self.assertRaisesRegex(ValueError, "Pi 0.87.0.*required"):
                ralph.check_agent("pi")

    def adapter_payload(self, agent, run, meta):
        text = lambda value: [{"type": "text", "text": value}]
        self.h.report(run, "blocked")
        message = "RALPH_REPORT:" + ralph.read(run / "result.json")["report_id"]
        payload = {"agent": agent, "adapter": str(SCRIPT.parent / f"adapters/{agent}.mjs"),
                   "cwd": str(self.repo), "session": {"id": "main-session"}}
        if agent == "opencode":
            payload.update(event={"type": "session.status", "properties": {
                "sessionID": "main-session", "status": {"type": "idle"}}}, messages=[
                    {"info": {"role": "user"}, "parts": text(meta["prompt"])},
                    {"info": {"role": "assistant", "id": "final", "finish": "stop", "time": {"completed": 1}},
                     "parts": text(message)}])
        else:
            payload.update(file=str(run / "pi-session.jsonl"), event={"type": "agent_settled"}, entries=[
                {"type": "message", "id": "user", "message": {"role": "user", "content": text(meta["prompt"])}},
                {"type": "message", "id": "final", "message": {
                    "role": "assistant", "content": text(message), "stopReason": "stop"}}])
        return payload

    def emit_adapter(self, run, payload):
        result = subprocess.run(["node", str(FIXTURES / "adapter_event.mjs")], input=json.dumps(payload),
                                env=dict(os.environ, TMUX_RALPH_RUN=str(run)), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which("node"), "requires Node.js for runtime adapters")
    def test_opencode_filters_children_wrong_prompts_and_incomplete_turns(self):
        run, meta = self.prepare("opencode")
        valid = self.adapter_payload("opencode", run, meta)
        cases = []
        child = copy.deepcopy(valid)
        child["session"]["parentID"] = "parent"
        cases.append(child)
        other = copy.deepcopy(valid)
        other["messages"][0]["parts"][0]["text"] = "Other prompt"
        cases.append(other)
        busy = copy.deepcopy(valid)
        busy["event"]["properties"]["status"]["type"] = "busy"
        cases.append(busy)
        race = copy.deepcopy(valid)
        race["busyDuringRead"] = True
        cases.append(race)
        for changes in ({"finish": "tool-calls"}, {"error": {"name": "Aborted"}},
                        {"summary": True}, {"time": {}}):
            bad = copy.deepcopy(valid)
            bad["messages"][-1]["info"].update(changes)
            cases.append(bad)
        pending = copy.deepcopy(valid)
        pending["messages"].append({"info": {"role": "user"}, "parts": [{"type": "text", "text": "Continue"}]})
        cases.append(pending)
        for payload in cases:
            self.emit_adapter(run, payload)
            self.assertIsNone(ralph.completion(run))
        self.emit_adapter(run, valid)
        self.assertEqual(ralph.completion(run)["event"], ["main-session", "final"])
        self.emit_adapter(run, valid)
        self.assertEqual(ralph.completion(run)["outcome"], "blocked")

    @unittest.skipUnless(shutil.which("node"), "requires Node.js for runtime adapters")
    def test_pi_waits_for_settled_owned_session_and_final_response(self):
        run, meta = self.prepare("pi")
        valid = self.adapter_payload("pi", run, meta)
        cases = []
        for changes in ({"event": {"type": "agent_end"}}, {"file": str(self.repo / "other-session.jsonl")},
                        {"idle": False}, {"pending": True}):
            bad = copy.deepcopy(valid)
            bad.update(changes)
            cases.append(bad)
        for reason in ("toolUse", "aborted", "error", "length"):
            bad = copy.deepcopy(valid)
            bad["entries"][-1]["message"]["stopReason"] = reason
            cases.append(bad)
        wrong = copy.deepcopy(valid)
        wrong["entries"][0]["message"]["content"] = "Other work"
        cases.append(wrong)
        for payload in cases:
            self.emit_adapter(run, payload)
            self.assertIsNone(ralph.completion(run))
        self.emit_adapter(run, valid)
        self.assertEqual(ralph.completion(run)["event"], ["main-session", "final"])

    @unittest.skipUnless(shutil.which("node"), "requires Node.js for runtime adapters")
    def test_opencode_callback_failure_is_retained_without_crashing_agent(self):
        run, meta = self.prepare("opencode")
        payload = self.adapter_payload("opencode", run, meta)
        payload["failure"] = True
        self.emit_adapter(run, payload)
        self.assertIsNone(ralph.completion(run))
        self.assertIn("Simulated API read failure", (run / "adapter-errors.log").read_text())

    def fake_agents(self, outcome="done"):
        folder = self.repo / "bin"
        folder.mkdir()
        for agent in ("claude", "opencode", "pi"):
            executable = folder / agent
            executable.write_text(f"#!{sys.executable}\n" + (FIXTURES / "fake_agent.py").read_text())
            executable.chmod(0o755)
        self.addCleanup(lambda: ralph.tmux(self.repo, "kill-server", check=False))
        env = patch.dict(os.environ, {"RALPH_TEST_OUTCOME": outcome,
                                     "RALPH_TEST_ADAPTER_FIXTURE": str(FIXTURES / "adapter_event.mjs")})
        env.start()
        self.addCleanup(env.stop)

    @unittest.skipUnless(shutil.which("tmux") and shutil.which("node"), "requires tmux and Node.js")
    def test_each_agent_implements_dependency_chain(self):
        self.fake_agents()
        for agent in ("claude", "opencode", "pi"):
            with self.subTest(agent=agent):
                self.h.ticket()
                self.h.ticket("02", "01")
                self.h.state = self.repo / agent
                result = self.h.invoke("run", "--agent", agent, "--prompt", "Use the implement skill.")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual([t["status"] for t in ralph.tickets(self.h.folder).values()], ["done", "done"])
                self.assertEqual(ralph.read(self.h.state / "state.json")["agent"], agent)
                self.assertEqual(len(list((self.h.state / "runs").iterdir())), 2)

    @unittest.skipUnless(shutil.which("tmux") and shutil.which("node"), "requires tmux and Node.js")
    def test_each_agent_can_resume_blocked_conversation_without_agent_flag(self):
        self.fake_agents("interactive")
        for agent in ("claude", "opencode", "pi"):
            with self.subTest(agent=agent):
                self.h.ticket()
                self.h.state = self.repo / agent
                result = self.h.invoke("start", "--agent", agent)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.h.wait_for(lambda: bool(list((self.h.state / "runs").glob("*/progress.json"))))
                run, = (self.h.state / "runs").iterdir()
                meta = ralph.read(run / "run.json")
                self.assertEqual(self.h.invoke("stop").returncode, 0)
                dead = lambda: ralph.tmux(self.repo, "display-message", "-p", "-t", "loop", "#{pane_dead}").stdout.strip() == "1"
                self.h.wait_for(dead)
                self.assertEqual(self.h.invoke("start").returncode, 0)
                ralph.tmux(self.repo, "send-keys", "-t", meta["session"], "-l", "I fixed it; continue")
                ralph.tmux(self.repo, "send-keys", "-t", meta["session"], "Enter")
                self.h.wait_for(lambda: "All tickets done." in (self.h.state / "loop.log").read_text())
                self.h.wait_for(dead)
                self.assertEqual(len(list((self.h.state / "runs").iterdir())), 1)
                self.assertEqual(len(list((run / "reports").glob("*.json"))), 2)
                self.assertEqual(ralph.tickets(self.h.folder)["01"]["status"], "done")


if __name__ == "__main__":
    unittest.main()
