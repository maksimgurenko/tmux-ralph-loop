import os
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch

import test_ralph
from trackers import ApiError, BLOCKED_LABEL, RemoteIssues, remote_config

ralph = test_ralph.ralph
FIXTURE = Path(__file__).parent / "fixtures/fake_tracker.py"


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.h = test_ralph.RalphTests()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.repo, self.state = self.h.repo, self.h.state
        self.store = self.repo / "tracker.json"
        self.bin = self.repo / "bin"
        self.bin.mkdir()
        for cli in ("gh", "glab"):
            (self.bin / cli).write_text(f"#!{sys.executable}\nimport runpy\nrunpy.run_path({str(FIXTURE)!r}, run_name='__main__')\n")
            (self.bin / cli).chmod(0o755)
        self.env = patch.dict(os.environ, RALPH_TEST_TRACKER=str(self.store),
                              PATH=str(self.bin) + os.pathsep + os.environ["PATH"])
        self.env.start()
        self.addCleanup(self.env.stop)

    def setup_tracker(self, tracker="github", numbers=("123",), links=None, bodies=None):
        self.tracker = tracker
        github = tracker == "github"
        self.project = "org/project" if github else "group/subgroup/project"
        self.config = remote_config(tracker, self.project, None, self.repo)
        issues = {}
        for number in numbers:
            issues[number] = dict(title="Task " + number, state="open" if github else "opened",
                                  labels=[{"name": "ready-for-agent"}] if github else ["ready-for-agent"])
            body = (bodies or {}).get(number, "## Acceptance criteria\n- [ ] Verify behavior\n")
            issues[number].update({"number" if github else "iid": int(number),
                                   "body" if github else "description": body,
                                   "html_url" if github else "web_url":
                                       f"https://{tracker}.com/{self.project}/{'issues' if github else '-/issues'}/{number}"})
        ralph.write(self.store, dict(tracker=tracker, issues=issues, links=links or {}))
        return RemoteIssues(self.config, self.repo)

    def update(self, **fields):
        data = ralph.read(self.store)
        data.update(fields)
        ralph.write(self.store, data)

    def invoke(self, command, *args, state=None):
        return self.h.cli(command, "--repo", str(self.repo), "--state", str(state or self.state), *args)

    def prepare(self, source):
        return ralph.prepare(self.repo, self.state, None, {}, 30, source=self.config, found=source.load())

    def test_native_and_text_dependencies_gate_native_numbers_and_cross_project_blockers(self):
        for tracker in ("github", "gitlab"):
            with self.subTest(tracker=tracker):
                source = self.setup_tracker(tracker, ("123", "350"), links={"350": ["123"]})
                found = source.load()
                self.assertEqual(ralph.frontier(found), ["123"])
                self.assertTrue(found["350"]["blocked"])
                data = ralph.read(self.store)
                data["issues"]["123"]["state"] = "closed"
                field = "body" if tracker == "github" else "description"
                data["issues"]["350"][field] += "\n## Blocked by\n- other/project#77\n"
                root = "repos/other/project" if tracker == "github" else "projects/other/project"
                data["responses"] = {root + "/issues/77": dict(data["issues"]["123"], state="closed")}
                ralph.write(self.store, data)
                self.assertEqual(ralph.frontier(source.load()), ["350"])
                self.assertTrue(any(request["endpoint"] == root + "/issues/77"
                                    for request in ralph.read(self.store)["requests"]))

    def test_body_blockers_support_matt_fields_and_urls_without_treating_parents_as_blockers(self):
        for tracker in ("github", "gitlab"):
            with self.subTest(tracker=tracker):
                source = self.setup_tracker(tracker, ("123", "350"))
                data = ralph.read(self.store)
                url = data["issues"]["123"]["html_url" if tracker == "github" else "web_url"]
                for declaration in ("**Blocked by:** #123", "Blocked by: " + url,
                                    "## Blocked by\n- #123\n\n## Notes\nPart of #999"):
                    dependencies = source.text_dependencies(declaration)
                    self.assertEqual(len(dependencies), 1)
                    self.assertFalse(source.closed(dependencies[0]))
                self.assertIsNone(source.text_dependencies("Part of #999\n- [ ] Fix #123"))
                self.assertEqual(source.text_dependencies("**Blocked by:** None (can start immediately)."), [])
                with self.assertRaisesRegex(ValueError, "Unrecognized"):
                    source.text_dependencies("## Blocked by\nWaiting for someone")

    def test_dependency_api_errors_fail_closed_with_explicit_fallback_for_older_servers(self):
        for tracker in ("github", "gitlab"):
            with self.subTest(tracker=tracker):
                source = self.setup_tracker(tracker)
                endpoint = f"{source.root}/issues/123/" + ("dependencies/blocked_by" if source.github else "links")
                # Fake logs decoded paths, while overrides are matched after decoding.
                from urllib.parse import unquote
                endpoint = unquote(endpoint)
                self.update(errors={endpoint: 403})
                with self.assertRaises(ApiError):
                    source.load()
                self.update(errors={endpoint: 404})
                with self.assertRaises(ApiError):
                    source.load()
                data = ralph.read(self.store)
                field = "body" if source.github else "description"
                data["issues"]["123"][field] += "\nBlocked by: None\n"
                ralph.write(self.store, data)
                self.assertEqual(ralph.frontier(source.load()), ["123"])
                self.update(errors={endpoint: 503})
                with self.assertRaises(ApiError):
                    source.load()

    def test_github_prs_are_excluded_and_hidden_open_blockers_still_block(self):
        source = self.setup_tracker(numbers=("123", "350"))
        data = ralph.read(self.store)
        data["issues"]["123"]["pull_request"] = {}
        data["issues"]["350"]["issue_dependencies_summary"] = {"blocked_by": 1}
        ralph.write(self.store, data)
        found = source.load()
        self.assertEqual(list(found), ["350"])
        self.assertEqual(ralph.frontier(found), [])

    def test_pagination_and_all_label_filters(self):
        source = self.setup_tracker(numbers=tuple(str(n) for n in range(1, 102)))
        found = source.load()
        self.assertEqual(len(found), 101)
        requests = ralph.read(self.store)["requests"]
        lists = [r for r in requests if r["endpoint"] == source.root + "/issues"]
        self.assertEqual([r["query"]["page"] for r in lists], [["1"], ["2"]])
        self.config["labels"] = ["ready-for-agent", "feature"]
        self.assertEqual(RemoteIssues(self.config, self.repo).load(), {})

    def test_gitlab_reads_unpaginated_native_links_once_even_at_page_size(self):
        source = self.setup_tracker("gitlab")
        issue = ralph.read(self.store)["issues"]["123"]
        links = [dict(issue, iid=n, state="closed", link_type="is_blocked_by") for n in range(1, 101)]
        links[-1]["state"] = "opened"
        endpoint = "projects/group/subgroup/project/issues/123/links"
        self.update(responses={endpoint: links})
        self.assertEqual(ralph.frontier(source.load()), [])
        requests = [r for r in ralph.read(self.store)["requests"] if r["endpoint"] == endpoint]
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["query"], {})

    def test_completion_requires_remote_original_checked_criteria_and_rechecks_blockers(self):
        for tracker in ("github", "gitlab"):
            with self.subTest(tracker=tracker):
                source = self.setup_tracker(tracker, ("123", "350"))
                run, meta = self.prepare(source)
                self.h.report(run, number="123")
                self.h.event(run, meta)
                self.assertEqual(ralph.completion(run)["outcome"], "blocked")
                data = ralph.read(self.store)
                field = "body" if source.github else "description"
                original = data["issues"]["123"][field]
                data["issues"]["123"][field] = original.replace("[ ]", "[x]")
                ralph.write(self.store, data)
                self.assertEqual(ralph.completion(run)["outcome"], "done")
                data["links"] = {"123": ["350"]}
                ralph.write(self.store, data)
                self.assertIn("open blockers", ralph.completion(run)["summary"])
                data["links"] = {}
                data["issues"]["123"][field] = "- [x] Easier criterion\n"
                ralph.write(self.store, data)
                self.assertIn("criteria changed", ralph.completion(run)["summary"])
                self.assertNotEqual(ralph.read(self.store)["issues"]["123"]["state"], "closed")
                self.assertIn("issue number without #", meta["prompt"])
                self.assertIn("issues.json", meta["prompt"])
                self.assertNotIn("including its checkbox", meta["prompt"])

    def test_issue_without_acceptance_criteria_cannot_dispatch(self):
        source = self.setup_tracker(bodies={"123": "Implement something"})
        with self.assertRaisesRegex(ValueError, "no acceptance checkboxes"):
            self.prepare(source)

    def test_remote_marking_preserves_labels_and_uses_native_close(self):
        for tracker in ("github", "gitlab"):
            with self.subTest(tracker=tracker):
                source = self.setup_tracker(tracker)
                source.mark("123", "blocked")
                self.assertEqual(source.current("123")["status"], "blocked")
                source.mark("123", "ready-for-agent")
                self.assertEqual(source.current("123")["status"], "ready-for-agent")
                source.mark("123", "done")
                source.mark("123", "done")  # Finalization can safely repeat on restart.
                data = ralph.read(self.store)
                self.assertEqual(data["issues"]["123"]["state"], "closed")
                names = ([x["name"] for x in data["issues"]["123"]["labels"]] if source.github
                         else data["issues"]["123"]["labels"])
                self.assertEqual(names, ["ready-for-agent"])
                self.assertFalse(any("comments" in r["endpoint"] or "notes" in r["endpoint"] for r in data["requests"]))
                close = data["requests"][-1]
                self.assertEqual(close["method"], "PATCH" if source.github else "PUT")
                self.assertEqual(close["payload"], {"state": "closed", "state_reason": "completed"}
                                 if source.github else {"state_event": "close", "remove_labels": BLOCKED_LABEL})

    def test_project_inference_self_hosted_urls_and_source_configuration_conflicts(self):
        self.setup_tracker()
        for tracker, expected in (("github", "org/project"), ("gitlab", "group/project")):
            self.assertEqual(remote_config(tracker, None, None, self.repo)["project"], expected)
        config = remote_config("gitlab", "https://gitlab.example.com/group/sub/project.git", ["ready", "feature"], self.repo)
        self.assertEqual(config["host"], "gitlab.example.com")
        self.assertEqual(config["project"], "group/sub/project")
        for project in ("https://user:secret@example.com/org/repo", "https://example.com/org/repo?x=1", "org/../repo"):
            with self.assertRaises(ValueError):
                remote_config("github", project, None, self.repo)
        ralph.write(self.state / "source.json", self.config)
        result = self.invoke("status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("github github.com/org/project", result.stdout)
        self.assertIn("123", result.stdout)
        for args in (("--tracker", "gitlab", "--project", "group/project"),
                     ("--label", "another-scope"), ("--project", "other/project"), ("--tickets", str(self.h.folder))):
            result = self.invoke("status", *args)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(ralph.read(self.state / "source.json"), self.config)

    def test_status_is_read_only_and_stop_works_without_tracker_access(self):
        self.setup_tracker()
        result = self.invoke("status", "--tracker", "github", "--project", self.project)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.state.exists())
        self.assertTrue(all(r["method"] == "GET" for r in ralph.read(self.store)["requests"]))
        self.store.unlink()
        result = self.invoke("stop", "--tracker", "gitlab")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.state / "STOP").exists())

    def test_status_retains_recovery_commands_during_api_failure(self):
        source = self.setup_tracker()
        ralph.write(self.state / "source.json", self.config)
        self.update(errors={source.root + "/issues": 503})
        result = self.invoke("status")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Cannot refresh issues", result.stderr)
        self.assertIn("Runner attach:", result.stdout)

    def test_legacy_local_attempt_counts_cannot_be_reused_for_remote_issues(self):
        self.setup_tracker()
        checkpoint = {"active": None, "attempts": {"10": 3}}
        ralph.write(self.state / "state.json", checkpoint)
        result = self.invoke("run", "--tracker", "github", "--project", self.project)
        self.assertEqual(result.returncode, 2)
        self.assertIn("no recorded issue source", result.stderr)
        self.assertFalse((self.state / "source.json").exists())
        self.assertFalse((self.state / "runs").exists())
        self.assertEqual(ralph.read(self.state / "state.json"), checkpoint)

    @unittest.skipUnless(shutil.which("tmux"), "requires tmux")
    def test_blocked_remote_session_accepts_followup_and_clears_label(self):
        source = self.setup_tracker()
        self.h.fake_codex("interactive")
        result = self.invoke("start", "--tracker", "github", "--project", self.project, "--timeout", "20")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.h.wait_for(lambda: bool(list((self.state / "runs").glob("*/progress.json"))))
        run, = (self.state / "runs").iterdir()
        meta = ralph.read(run / "run.json")
        self.assertEqual(source.current("123")["status"], "blocked")
        status = self.invoke("status")
        self.assertIn("Waiting for input:", status.stdout)
        ralph.tmux(self.repo, "send-keys", "-t", meta["session"], "-l", "Continue")
        ralph.tmux(self.repo, "send-keys", "-t", meta["session"], "Enter")
        self.h.wait_for(lambda: "No open issues match" in (self.state / "loop.log").read_text())
        self.assertEqual(source.current("123")["status"], "done")
        self.assertEqual(len(list((self.state / "runs").iterdir())), 1)
        self.assertEqual(ralph.read(self.state / "state.json")["attempts"], {"123": 1})
        labels = [label["name"] for label in ralph.read(self.store)["issues"]["123"]["labels"]]
        self.assertNotIn(BLOCKED_LABEL, labels)

    @unittest.skipUnless(shutil.which("tmux"), "requires tmux")
    def test_both_remote_trackers_run_dependent_issues_in_real_tmux(self):
        self.h.fake_codex("done")
        for tracker in ("github", "gitlab"):
            with self.subTest(tracker=tracker):
                self.setup_tracker(tracker, ("123", "350"), links={"350": ["123"]})
                state = self.repo / tracker
                result = self.invoke("run", "--tracker", tracker, "--project", self.project, "--timeout", "20", state=state)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertLess(result.stdout.index("123: done"), result.stdout.index("350: done"))
                self.assertEqual([i["state"] for i in ralph.read(self.store)["issues"].values()], ["closed", "closed"])
                self.assertEqual(ralph.read(state / "state.json")["attempts"], {"123": 1, "350": 1})
                self.assertEqual(len(list((state / "runs").iterdir())), 2)

    @unittest.skipUnless(shutil.which("tmux"), "requires tmux")
    def test_detached_start_remembers_source_and_failed_close_resumes_same_run(self):
        self.setup_tracker("gitlab")
        self.update(fail_close_once=True)
        self.h.fake_codex("done")
        result = self.invoke("start", "--tracker", "gitlab", "--project", self.project, "--timeout", "20")
        self.assertEqual(result.returncode, 0, result.stderr)
        log = self.state / "loop.log"
        self.h.wait_for(lambda: log.exists() and "HTTP 503" in log.read_text())
        self.h.wait_for(lambda: ralph.tmux(self.repo, "display-message", "-p", "-t", "loop", "#{pane_dead}").stdout.strip() == "1")
        self.assertIsNotNone(ralph.read(self.state / "state.json")["active"])
        result = self.invoke("start")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.h.wait_for(lambda: "No open issues match" in log.read_text())
        self.assertEqual(len(list((self.state / "runs").iterdir())), 1)
        self.assertIsNone(ralph.read(self.state / "state.json")["active"])
        self.assertEqual(ralph.read(self.state / "state.json")["attempts"], {"123": 1})
        self.assertEqual(ralph.read(self.store)["issues"]["123"]["state"], "closed")

    def test_retry_native_number_removes_blocked_label_and_keeps_scope(self):
        source = self.setup_tracker("gitlab")
        source.mark("123", "blocked")
        ralph.write(self.state / "source.json", self.config)
        result = self.invoke("retry", "123")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(source.current("123")["status"], "ready-for-agent")
        self.assertEqual(ralph.read(self.state / "state.json")["attempts"], {"123": 0})
        self.assertEqual(ralph.read(self.state / "source.json"), self.config)


if __name__ == "__main__":
    unittest.main()
