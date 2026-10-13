#!/usr/bin/env python3
"""Client-assets git-source regressions. No Nix, credentials or network needed.
Run: python3 tests/ultimator-cluster-assets.py
Exercises the unit's shell script, substituting its state directory and Git fixture.
Nix evaluation of inherited authentication is a separate production-host check.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / "feat/ultimator-release-build.nix"
FORGE = "https://ultimator.app/git/ulti/ultimator.git"
WANTED = "a" * 40


class AssetsFetch(unittest.TestCase):
    def exercise(self, *, existing=False, cached=False, token=True, hold=False, built=False):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            release = root / "release"
            checkout = release / "assets/ultimator"
            release.mkdir()
            if existing:
                (checkout / ".git").mkdir(parents=True)
                (checkout / "fixture-origin").write_text("git@github.com:xmit-dev/ultimator.git")
            if token:
                (release / "forge-read-token").write_text("fixture-not-a-credential")
            if hold:
                (release / "hold").write_text("fixture hold")
            (release / "assets-wanted").write_text(WANTED)
            if built:
                (release / "assets").mkdir(exist_ok=True)
                (release / "assets/built").write_text(WANTED)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            git = bin_dir / "git"
            git.write_text('''#!/usr/bin/env bash
set -eu
printf '%s\\n' "$*" >> "$FIXTURE_GIT_LOG"
case "$1" in
  clone)
    [ "$4" = "https://ultimator.app/git/ulti/ultimator.git" ] || exit 91
    mkdir -p "$5/.git"
    ;;
  remote)
    [ "$2 $3 $4" = "set-url origin https://ultimator.app/git/ulti/ultimator.git" ] || exit 92
    printf '%s' "$4" > fixture-origin
    ;;
  cat-file) [ "$FIXTURE_CACHED" = 1 ] ;;
  fetch)
    [ "$(cat fixture-origin)" = "https://ultimator.app/git/ulti/ultimator.git" ] || exit 93
    ;;
  checkout)
    mkdir -p deploy
    printf '#!/bin/sh\\n[ "$1" = --assets-only ]\\n' > deploy/cd.sh
    chmod +x deploy/cd.sh
    ;;
  clean) ;;
  *) exit 94 ;;
esac
''')
            git.chmod(0o755)
            text = MODULE.read_text()
            start = text.index('  systemd.services.ultimator-cluster-assets = {')
            script = text[text.index("    script = ''\n", start) + len("    script = ''\n"):]
            script = script[:script.index("    '';\n")]
            script = script.replace("${release}", str(release)).replace("''${", "${")
            env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}",
                       FIXTURE_CACHED="1" if cached else "0", FIXTURE_GIT_LOG=str(root / "git.log"))
            result = subprocess.run(["bash", "-euc", script], env=env, capture_output=True, text=True)
            log = (root / "git.log").read_text() if (root / "git.log").exists() else ""
            output = (release / "assets/built").read_text() if (release / "assets/built").exists() else None
            origin = (checkout / "fixture-origin").read_text() if (checkout / "fixture-origin").exists() else None
            return result, log, output, origin

    def test_new_checkout_clones_forge(self):
        result, log, built, _ = self.exercise()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"clone --quiet --no-checkout {FORGE}", log)
        self.assertNotIn("github.com", log)
        self.assertEqual(built.strip(), WANTED)

    def test_existing_github_origin_is_repointed_before_fetch(self):
        result, log, built, origin = self.exercise(existing=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(log.index("remote set-url"), log.index("fetch --quiet origin main"))
        self.assertNotIn("clone", log)
        self.assertEqual(origin, FORGE)
        self.assertEqual(built.strip(), WANTED)

    def test_cached_commit_still_repoints_origin(self):
        result, log, built, origin = self.exercise(existing=True, cached=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("fetch", log)
        self.assertEqual(origin, FORGE)
        self.assertEqual(built.strip(), WANTED)

    def test_no_token_fails_before_any_git(self):
        result, log, built, _ = self.exercise(token=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Forge read token", result.stdout)
        self.assertEqual(log, "")
        self.assertIsNone(built)

    def test_hold_and_already_built_are_noops(self):
        for case in ({"hold": True}, {"built": True}):
            result, log, _, _ = self.exercise(token=False, **case)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(log, "")


if __name__ == "__main__":
    unittest.main()
