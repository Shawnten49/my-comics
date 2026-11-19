from __future__ import annotations

import unittest
from unittest.mock import patch
from pathlib import Path

from comic_web.chrome_cdp import ChromeCDPError, ensure_chrome_cdp


class ChromeCDPTest(unittest.TestCase):
    def test_existing_cdp_is_reused(self) -> None:
        launched = []
        changed = ensure_chrome_cdp(
            "http://127.0.0.1:9222",
            launcher=lambda command: launched.append(command),
            availability_check=lambda url, timeout: True,
            sleeper=lambda seconds: None,
        )
        self.assertFalse(changed)
        self.assertEqual([], launched)

    def test_missing_local_cdp_launches_chrome(self) -> None:
        launched = []
        available = {"value": False}

        def availability_check(url: str, timeout: float) -> bool:
            return available["value"]

        def launcher(command: list[str]) -> None:
            launched.append(command)
            available["value"] = True

        changed = ensure_chrome_cdp(
            "http://127.0.0.1:9222",
            profile_dir=Path("/tmp/comic-chrome-test-profile"),
            launcher=launcher,
            availability_check=availability_check,
            sleeper=lambda seconds: None,
        )
        self.assertTrue(changed)
        self.assertEqual(1, len(launched))
        command = launched[0]
        self.assertIn("--remote-debugging-port=9222", command)
        self.assertTrue(
            any(item.startswith("--user-data-dir=") for item in command)
        )

    def test_remote_cdp_is_not_launched_locally(self) -> None:
        with self.assertRaises(ChromeCDPError):
            ensure_chrome_cdp(
                "http://192.168.1.20:9222",
                launcher=lambda command: None,
                availability_check=lambda url, timeout: False,
                sleeper=lambda seconds: None,
            )

    def test_environment_controls_default_profile(self) -> None:
        launched = []
        available = {"value": False}

        def availability_check(url: str, timeout: float) -> bool:
            return available["value"]

        def launcher(command: list[str]) -> None:
            launched.append(command)
            available["value"] = True

        with patch.dict(
            "os.environ",
            {"COMIC_CHROME_PROFILE_DIR": "/tmp/comic-custom-profile"},
            clear=False,
        ):
            ensure_chrome_cdp(
                "http://127.0.0.1:9222",
                launcher=launcher,
                availability_check=availability_check,
                sleeper=lambda seconds: None,
            )

        self.assertIn(
            f"--user-data-dir={Path('/tmp/comic-custom-profile').resolve()}",
            launched[0],
        )


if __name__ == "__main__":
    unittest.main()
