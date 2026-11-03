"""Exercise the publishing step with simulated GitHub release state."""
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


class ReleaseTests(unittest.TestCase):
    def publish(self, *, exists=False, checksum=True):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml").read_text()
        step = workflow.split("      - name: Publish tested release\n", 1)[1]
        script = textwrap.dedent(step.split("        run: |\n", 1)[1])
        with tempfile.TemporaryDirectory(prefix="ralph publish ") as temporary:
            root = Path(temporary)
            (root / "dist").mkdir()
            calls = root / "github-calls"
            calls.touch()
            # Isolate external commands so these tests cannot publish a release.
            simulated = r'''
sha256sum() { test "$CHECKSUM_OK" = 1; }
gh() {
    printf '%s\n' "$*" >> "$GH_CALLS"
    case "$1 $2" in
        "release view") test "$RELEASE_EXISTS" = 1 ;;
        "release create")
            if test "$RELEASE_EXISTS" = 1; then
                echo 'release already exists' >&2
                return 1
            fi ;;
        "release upload") test "$RELEASE_EXISTS" = 1 ;;
        *) return 2 ;;
    esac
}
'''
            env = dict(os.environ, RELEASE_TAG="v0.1.0", GH_CALLS=str(calls),
                       RELEASE_EXISTS="1" if exists else "0", CHECKSUM_OK="1" if checksum else "0")
            result = subprocess.run(["bash", "-euo", "pipefail", "-c", simulated + script],
                                    cwd=root, env=env, text=True, capture_output=True, timeout=10)
            return result, calls.read_text().splitlines()

    def test_existing_release_receives_artifacts_without_recreating_it(self):
        result, calls = self.publish(exists=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("release upload v0.1.0 ralph SHA256SUMS --clobber", calls)
        self.assertFalse(any(call.startswith("release create ") for call in calls))

    def test_new_release_includes_artifacts_and_generated_notes(self):
        result, calls = self.publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("release create v0.1.0 ralph SHA256SUMS --verify-tag --generate-notes", calls)
        self.assertFalse(any(call.startswith("release upload ") for call in calls))

    def test_checksum_failure_prevents_release_changes(self):
        result, calls = self.publish(checksum=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
