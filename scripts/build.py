#!/usr/bin/env python3
"""Build the portable executable and its release checksum using only the stdlib."""
import argparse
import hashlib
from pathlib import Path
import re
import shutil
import tempfile
import zipapp


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="dev", help="Release tag vX.Y.Z (default: dev)")
    parser.add_argument("--output-dir", type=Path, default=root / "dist")
    args = parser.parse_args()
    if args.version != "dev" and not re.fullmatch(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", args.version):
        parser.error("version must be dev or a release tag vX.Y.Z")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    executable = args.output_dir / "ralph"
    with tempfile.TemporaryDirectory(prefix="ralph-build-") as temporary:
        staging = Path(temporary)
        for name in ("ralph.py", "trackers.py"):
            shutil.copyfile(root / "scripts" / name, staging / name)
        shutil.copytree(root / "scripts/adapters", staging / "adapters")
        shutil.copyfile(root / "LICENSE", staging / "LICENSE")
        (staging / "_version.py").write_text(f"VERSION = {args.version!r}\n", encoding="utf-8")
        zipapp.create_archive(staging, executable, interpreter="/usr/bin/env python3",
                              main="ralph:cli", compressed=True,
                              filter=lambda path: "__pycache__" not in path.parts
                              and path.suffix not in {".pyc", ".pyo"})
    executable.chmod(0o755)
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    (args.output_dir / "SHA256SUMS").write_text(f"{digest}  ralph\n", encoding="ascii")
    print(f"Built {executable} ({args.version})")


if __name__ == "__main__":
    main()
