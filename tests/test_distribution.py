"""Release contents and the exact installation recipe, using temporary homes."""
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

import test_ralph

ROOT = Path(__file__).resolve().parents[1]


class DistributionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix="ralph distribution ")
        cls.addClassCleanup(temporary.cleanup)
        cls.root = Path(temporary.name).resolve()
        supplied = os.environ.get("RALPH_TEST_ARTIFACT") or os.environ.get("RALPH_EXECUTABLE")
        if supplied:
            cls.artifact = Path(supplied).resolve()
            cls.version = os.environ.get("RALPH_EXPECTED_VERSION", test_ralph.ralph.VERSION)
        else:
            cls.version = "v1.2.3"
            cls.artifact = cls.root / "release files/ralph"
            subprocess.run([sys.executable, str(ROOT / "scripts/build.py"), "--version", cls.version,
                            "--output-dir", str(cls.artifact.parent)], check=True, capture_output=True)
        cls.checksum = cls.artifact.with_name("SHA256SUMS")
        guide = (ROOT / "INSTALL.agent.md").read_text()
        cls.recipe = re.search(r"```bash\n(.*?)\n```", guide, re.S)[1]

    def test_archive_is_compressed_and_contains_runtime_adapters_and_license(self):
        self.assertEqual(self.artifact.read_bytes().splitlines()[0], b"#!/usr/bin/env python3")
        with zipfile.ZipFile(self.artifact) as archive:
            self.assertEqual(set(archive.namelist()), {
                "__main__.py", "ralph.py", "trackers.py", "_version.py", "LICENSE",
                "adapters/", "adapters/bridge.mjs", "adapters/opencode.mjs",
                "adapters/opencode/", "adapters/opencode/index.js", "adapters/pi.mjs"})
            self.assertTrue(all(info.compress_type == zipfile.ZIP_DEFLATED
                                for info in archive.infolist() if not info.is_dir()))
            self.assertIn("MIT License", archive.read("LICENSE").decode())
        digest = hashlib.sha256(self.artifact.read_bytes()).hexdigest()
        self.assertEqual(self.checksum.read_text(), f"{digest}  ralph\n")

    def install(self, scope, *, corrupt=False, pinned=False, existing=False):
        temporary = tempfile.TemporaryDirectory(prefix="installation ", dir=self.root)
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        home, project, mock_bin = root / "user home", root / "project space", root / "mock bin"
        for directory in (home, project, mock_bin):
            directory.mkdir()
        destination = home / ".local/bin/ralph" if scope == "global" else project / "ralph"
        if existing:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"existing installation\n")
        curl = mock_bin / "curl"
        curl.write_text(f"#!{sys.executable}\n" + '''import json, os, pathlib, shutil, sys
args = sys.argv[1:]
url = next(arg for arg in args if arg.startswith("https://"))
with open(os.environ["MOCK_CURL_LOG"], "a") as log:
    log.write(url + "\\n")
if url.endswith("/releases/latest"):
    print(json.dumps({"tag_name": "v1.2.3"}))
else:
    name = url.rsplit("/", 1)[1]
    target = pathlib.Path(args[args.index("-o") + 1])
    shutil.copyfile(pathlib.Path(os.environ["MOCK_RELEASE_DIR"]) / name, target)
    if name == "ralph" and os.environ.get("MOCK_CORRUPT") == "1":
        target.write_bytes(target.read_bytes() + b"corrupted")
''')
        curl.chmod(0o755)
        env = dict(os.environ, HOME=str(home), RALPH_DEST=str(destination),
                   MOCK_RELEASE_DIR=str(self.artifact.parent), MOCK_CURL_LOG=str(root / "downloads"),
                   MOCK_CORRUPT="1" if corrupt else "0",
                   PATH=os.pathsep.join((str(home / ".local/bin"), str(mock_bin), os.environ["PATH"])))
        env.pop("RALPH_VERSION", None)
        if pinned:
            env["RALPH_VERSION"] = "v1.2.3"
        result = subprocess.run(["bash", "-c", self.recipe], cwd=project, env=env,
                                text=True, capture_output=True, timeout=30)
        return result, destination, project, env, (root / "downloads").read_text().splitlines()

    def test_global_and_project_installations_verify_and_execute_only_one_file(self):
        for scope in ("global", "project"):
            with self.subTest(scope=scope):
                result, destination, project, env, downloads = self.install(scope, pinned=scope == "project")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("ralph " + self.version, result.stdout)
                self.assertIn("usage: ralph", result.stdout)
                self.assertTrue(os.access(destination, os.X_OK))
                self.assertEqual(list(destination.parent.iterdir()), [destination])
                command = "ralph" if scope == "global" else "./ralph"
                version = subprocess.run([command, "--version"], cwd=project, env=env,
                                         text=True, capture_output=True, check=True)
                self.assertEqual(version.stdout.strip(), "ralph " + self.version)
                self.assertFalse((project / ".ralph").exists())
                self.assertEqual(len(downloads), 3 if scope == "global" else 2)
                self.assertTrue(all("/v1.2.3/" in url for url in downloads if "/download/" in url))

    def test_checksum_mismatch_preserves_existing_installation_and_creates_no_new_file(self):
        for scope in ("global", "project"):
            for existing in (False, True):
                with self.subTest(scope=scope, existing=existing):
                    result, destination, project, _, _ = self.install(scope, corrupt=True, existing=existing)
                    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn("checksum verification failed", result.stderr)
                    if existing:
                        self.assertEqual(destination.read_bytes(), b"existing installation\n")
                    else:
                        self.assertFalse(destination.exists())
                    self.assertFalse((project / ".ralph").exists())


if __name__ == "__main__":
    unittest.main()
