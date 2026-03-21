"""Release script — bump version, merge dev → master, sync back.

Usage:
    python scripts/release.py <version>    # e.g. python scripts/release.py 1.2.3

Steps:
    1. Ensure working tree is clean
    2. Update VERSION file on dev
    3. Commit version bump on dev
    4. Checkout master, merge dev (creates release merge commit)
    5. Checkout dev, merge master back (fast-forward sync)
    6. Print summary — user decides when to push
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, **kwargs)
    if result.returncode != 0:
        print(f"FAIL: {' '.join(cmd)}")
        print(result.stderr.strip())
        sys.exit(1)
    return result


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/release.py <version>")
        print("Example: python scripts/release.py 1.2.3")
        sys.exit(1)

    version = sys.argv[1]

    # 1. Ensure clean working tree
    status = run(["git", "status", "--porcelain"]).stdout.strip()
    if status:
        print("ERROR: working tree is not clean. Commit or stash changes first.")
        print(status)
        sys.exit(1)

    # Ensure we're on dev
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    if branch != "dev":
        print(f"ERROR: must be on 'dev' branch (currently on '{branch}')")
        sys.exit(1)

    print(f"=== Release v{version} ===\n")

    # 2. Update VERSION
    version_file = ROOT / "VERSION"
    version_file.write_text(version + "\n")
    print(f"1. VERSION → {version}")

    # 3. Commit version bump on dev
    run(["git", "add", "VERSION"])
    run(["git", "commit", "-m", f"chore: bump version to {version}"])
    print(f"2. Committed version bump on dev")

    # 4. Merge dev → master
    run(["git", "checkout", "master"])
    run(["git", "merge", "dev", "-m", f"release: merge dev → master, v{version}"])
    print(f"3. Merged dev → master")

    # 5. Sync back: checkout dev, merge master (fast-forward)
    run(["git", "checkout", "dev"])
    run(["git", "merge", "master"])
    print(f"4. Synced master back to dev (fast-forward)")

    # Verify no divergence
    ahead = run(["git", "log", "--oneline", "dev..master"]).stdout.strip()
    if ahead:
        print(f"\nWARN: master still ahead of dev:\n{ahead}")
    else:
        print(f"\n=== v{version} release ready ===")
        print(f"master and dev are fully synced.")

    print(f"\nTo publish:")
    print(f"  git push origin master dev")


if __name__ == "__main__":
    main()
