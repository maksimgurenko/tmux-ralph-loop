import contextlib
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import test_ralph

ralph = test_ralph.ralph


class CliTests(unittest.TestCase):
    def setUp(self):
        self.h = test_ralph.RalphTests()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)

    def test_version_exits_without_accessing_a_tracker_or_starting_work(self):
        output = io.StringIO()
        with patch.object(sys, "argv", [str(test_ralph.SCRIPT), "--version"]), \
                contextlib.redirect_stdout(output), patch.object(ralph, "source_config") as source, \
                self.assertRaises(SystemExit) as error:
            ralph.main()
        self.assertEqual(error.exception.code, 0)
        self.assertEqual(output.getvalue().strip(), "ralph " + ralph.VERSION)
        source.assert_not_called()
        self.assertFalse(self.h.state.exists())

    def test_options_before_and_after_command_keep_values_and_defaults(self):
        parser = ralph.argument_parser()
        prefix = ["--repo", str(self.h.repo), "--state", str(self.h.state), "--agent", "claude",
                  "--tickets", str(self.h.folder), "--max-attempts", "2", "--timeout", "90"]
        before = parser.parse_args([*prefix, "start"])
        after = parser.parse_args(["start", *prefix])
        mixed = parser.parse_args([*prefix[:6], "start", *prefix[6:]])
        self.assertEqual(vars(before), vars(after))
        self.assertEqual(vars(before), vars(mixed))
        self.assertEqual(before.agent, "claude")
        self.assertEqual(before.max_attempts, 2)
        self.assertEqual(before.timeout, 90)
        defaults = parser.parse_args(["start"])
        self.assertEqual(defaults.repo, Path.cwd())
        self.assertEqual(defaults.max_attempts, 3)
        self.assertEqual(defaults.timeout, 28800)
        self.assertIsNone(defaults.agent)  # Keep recorded-agent lookup at runtime.

    def test_labels_on_both_sides_are_combined_and_prompt_modes_stay_exclusive(self):
        source = dict(tracker="github", host="github.com", project="owner/repo", labels=["ready", "feature"])
        argv = [str(test_ralph.SCRIPT), "--repo", str(self.h.repo), "--state", str(self.h.state),
                "--tracker", "github", "--label", "ready", "status", "--label", "feature"]
        with patch.object(sys, "argv", argv), patch.object(ralph, "source_config", return_value=source) as config, \
                patch.object(ralph, "load_tickets", return_value={}), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ralph.main(), 0)
            self.assertEqual(config.call_args.args[0].label, ["ready", "feature"])
        for prefix, suffix in ((["--prompt", "instructions"], ["--prompt-file", "missing.md"]),
                               (["--prompt-file", "missing.md"], ["--prompt", "instructions"])):
            with self.subTest(prefix=prefix), patch.object(sys, "argv", [str(test_ralph.SCRIPT), *prefix, "start", *suffix]), \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                ralph.main()
            self.assertEqual(error.exception.code, 2)
        self.assertFalse(self.h.state.exists())

    def test_help_for_every_command_exits_without_accessing_a_tracker_or_starting_work(self):
        for command in (None, "start", "run", "status", "stop", "retry"):
            with self.subTest(command=command):
                args = [str(test_ralph.SCRIPT), "--repo", str(self.h.repo), "--state", str(self.h.state),
                        "--tracker", "gitlab", "--prompt-file", str(self.h.repo / "missing.md")]
                if command:
                    args.append(command)
                args.append("-h")
                output = io.StringIO()
                with patch.object(sys, "argv", args), contextlib.redirect_stdout(output), \
                        patch.object(ralph, "source_config") as source, patch.object(ralph, "check_agent") as agent, \
                        self.assertRaises(SystemExit) as error:
                    ralph.main()
                self.assertEqual(error.exception.code, 0)
                source.assert_not_called()
                agent.assert_not_called()
                self.assertTrue(output.getvalue().startswith("usage: ralph " + (command or "COMMAND")))
                self.assertFalse(self.h.state.exists())
                self.assertFalse((self.h.repo / ".ralph").exists())


if __name__ == "__main__":
    unittest.main()
