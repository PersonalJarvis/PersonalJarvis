"""Execute the public Bash bootstrap through the Linux desktop-tool step.

The regular installer smoke starts at installer.py and cannot catch a Stage-1
shell exit before clone. These cases run the real shell prefix with harmless
package-manager and Git stubs, then stop before any network or venv work.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

INSTALL_SH = Path(__file__).resolve().parents[3] / "install" / "install.sh"
STOP_BEFORE_CLONE = 'if [ -d "$INSTALL_DIR/.git" ]; then'


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux bootstrap proof")
class LinuxStage1BootstrapTests(unittest.TestCase):
    def _assert_helper_preserves_script_stream(self, definition: str, invocation: str) -> None:
        script = (
            "set -euo pipefail\n"
            + definition
            + "\ndrain() { cat >/dev/null; }\n"
            + invocation
            + "\nprintf 'SCRIPT_CONTINUED\\n'\n"
        )
        result = subprocess.run(
            [shutil.which("bash") or "bash", "-s"],
            input=script,
            capture_output=True,
            text=True,
            encoding="utf-8",
            creationflags=NO_WINDOW_CREATIONFLAGS,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("SCRIPT_CONTINUED", result.stdout)

    def _run(
        self,
        *,
        headless: bool,
        display: bool,
        apt_succeeds: bool,
        apt_consumes_stdin: bool = False,
        pretend_unprivileged: bool = False,
    ) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stubs = root / "bin"
            stubs.mkdir()
            git = stubs / "git"
            git.write_text(
                "#!/bin/sh\n"
                '[ "$1" = "--version" ] && { echo "git version 2.47.3"; exit 0; }\n'
                "exit 99\n",
                encoding="utf-8",
            )
            git.chmod(0o755)
            apt = stubs / "apt-get"
            apt.write_text(
                "#!/bin/sh\n"
                + ("cat >/dev/null\n" if apt_consumes_stdin else "")
                + f"exit {0 if apt_succeeds else 1}\n",
                encoding="utf-8",
            )
            apt.chmod(0o755)
            # Hosted CI runners are non-root; their real sudo resets PATH and
            # would run the real apt-get instead of the harmless test stub.
            sudo = stubs / "sudo"
            sudo.write_text('#!/bin/sh\n"$@"\n', encoding="utf-8")
            sudo.chmod(0o755)
            if pretend_unprivileged:
                user_id = stubs / "id"
                user_id.write_text('#!/bin/sh\nprintf "1000\\n"\n', encoding="utf-8")
                user_id.chmod(0o755)

            source = INSTALL_SH.read_text(encoding="utf-8")
            self.assertIn(STOP_BEFORE_CLONE, source)
            bootstrap = source.split(STOP_BEFORE_CLONE, 1)[0]
            bootstrap += "\nprintf 'BOOTSTRAP_REACHED_FETCH\\n'\n"
            env = os.environ.copy()
            env.update(
                PATH=f"{stubs}{os.pathsep}{env['PATH']}",
                JARVIS_PYTHON=sys.executable,
                JARVIS_INSTALL_YES="1",
                JARVIS_INSTALL_PREREQS="auto",
                JARVIS_INSTALL_DIR=str(root / "install-target"),
            )
            if display:
                env["DISPLAY"] = ":99"
            else:
                env.pop("DISPLAY", None)
                env.pop("WAYLAND_DISPLAY", None)

            result = subprocess.run(
                [shutil.which("bash") or "bash", "-s", "--", *(["--headless"] if headless else [])],
                input=bootstrap,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                creationflags=NO_WINDOW_CREATIONFLAGS,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertIn("BOOTSTRAP_REACHED_FETCH", result.stdout)
            self.assertIn("2/6", result.stdout)
            return result.stdout

    def test_desktop_apt_success_continues_to_fetch(self) -> None:
        out = self._run(headless=False, display=True, apt_succeeds=True)
        self.assertIn("desktop automation tools installed", out)

    def test_desktop_apt_cannot_steal_the_piped_installer(self) -> None:
        out = self._run(
            headless=False,
            display=True,
            apt_succeeds=True,
            apt_consumes_stdin=True,
        )
        self.assertIn("desktop automation tools installed", out)

    def test_sudo_apt_cannot_steal_the_piped_installer(self) -> None:
        out = self._run(
            headless=False,
            display=True,
            apt_succeeds=True,
            apt_consumes_stdin=True,
            pretend_unprivileged=True,
        )
        self.assertIn("desktop automation tools installed", out)

    def test_desktop_apt_failure_still_continues_to_fetch(self) -> None:
        out = self._run(headless=False, display=True, apt_succeeds=False)
        self.assertIn("Some desktop tools could not be installed", out)

    def test_headless_skips_desktop_packages(self) -> None:
        out = self._run(headless=True, display=True, apt_succeeds=False)
        self.assertIn("Desktop automation tools skipped", out)

    def test_no_display_skips_desktop_packages(self) -> None:
        out = self._run(headless=False, display=False, apt_succeeds=False)
        self.assertIn("No graphical session detected", out)

    def test_spinner_child_cannot_steal_the_piped_installer(self) -> None:
        source = INSTALL_SH.read_text(encoding="utf-8")
        definition = "run_spin() {" + source.split("run_spin() {", 1)[1].split("# The mascot", 1)[0]
        self._assert_helper_preserves_script_stream(
            "note() { :; }\n" + definition, "run_spin 'test' drain\n"
        )

    def test_git_child_cannot_steal_the_piped_installer(self) -> None:
        source = INSTALL_SH.read_text(encoding="utf-8")
        definition = (
            "git_stream_pretty() {"
            + source.split("git_stream_pretty() {", 1)[1].split("clone_with_retry() {", 1)[0]
        )
        self._assert_helper_preserves_script_stream(
            "GIT_VERBOSITY='--quiet'\n" + definition, "pretty_git drain\n"
        )


if __name__ == "__main__":
    unittest.main()
