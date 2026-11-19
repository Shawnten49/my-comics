from __future__ import annotations

import unittest

from comic_web.sleep_inhibitor import SleepInhibitor


class FakeProcess:
    next_pid = 1000

    def __init__(self, command, **kwargs):
        self.command = command
        self.kwargs = kwargs
        self.pid = FakeProcess.next_pid
        FakeProcess.next_pid += 1
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


class SleepInhibitorTest(unittest.TestCase):
    def make_inhibitor(self, keep_display=False):
        created = []

        def factory(command, **kwargs):
            process = FakeProcess(command, **kwargs)
            created.append(process)
            return process

        inhibitor = SleepInhibitor(
            enabled=True,
            keep_display_awake=keep_display,
            popen_factory=factory,
        )
        inhibitor.supported = True
        return inhibitor, created

    def test_multiple_owners_share_one_caffeinate_process(self) -> None:
        inhibitor, created = self.make_inhibitor()
        inhibitor.acquire("download:1")
        inhibitor.acquire("batch:2")
        self.assertEqual(1, len(created))
        self.assertEqual(
            {"batch:2", "download:1"},
            set(inhibitor.status()["owners"]),
        )
        self.assertIn("-i", created[0].command)
        self.assertIn("-m", created[0].command)
        self.assertNotIn("-d", created[0].command)
        inhibitor.release("download:1")
        self.assertFalse(created[0].terminated)
        inhibitor.release("batch:2")
        self.assertTrue(created[0].terminated)
        self.assertFalse(inhibitor.status()["active"])

    def test_display_awake_option(self) -> None:
        inhibitor, created = self.make_inhibitor(keep_display=True)
        inhibitor.acquire("download:1")
        self.assertIn("-d", created[0].command)
        inhibitor.release_all()


if __name__ == "__main__":
    unittest.main()
