"""Reject application source in the platform Git index (also runs in CI)."""
import subprocess
import sys
from pathlib import PurePosixPath


def violations(paths):
    issues = []
    for path in paths:
        parts = PurePosixPath(path).parts
        if parts[0] in {"sites", "examples"} or parts[-1] in {"microsite.json", ".microsite-origin.json"}:
            issues.append(path)
    return issues


def main():
    paths = subprocess.check_output(["git", "ls-files", "-z"], text=True).split("\0")
    issues = violations([p for p in paths if p])
    if issues:
        print("Site files are forbidden in platform Git. Use microsite-container skill pull/deploy in an external workspace.")
        print("\n".join(issues))
        return 1
    print("Platform/site boundary check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
