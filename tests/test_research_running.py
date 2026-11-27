"""ResearchGate.running mirrors the live research thread and nothing else."""

import threading
import unittest

from warp3c.supervisor import ResearchGate


def _rest(view: dict) -> dict:
    copy = dict(view)
    copy.pop("running", None)
    return copy


class ResearchRunningTest(unittest.TestCase):
    def test_running_is_true_only_while_the_call_is_blocked(self):
        gate = ResearchGate()
        before = gate.status()
        self.assertIs(before["running"], False)
        started = threading.Event()
        release = threading.Event()

        def blocked():
            started.set()
            self.assertTrue(release.wait(2))
            return []

        self.assertTrue(gate.start(blocked))
        self.assertTrue(started.wait(1))
        during = gate.status()
        self.assertIs(during["running"], True)
        self.assertEqual(_rest(during), _rest(before))
        self.assertEqual(set(during), set(before))
        release.set()
        self.assertIsNotNone(gate._thread)
        gate._thread.join(2)
        after = gate.status()
        self.assertIs(after["running"], False)
        self.assertEqual(_rest(after), _rest(before))
        self.assertEqual(set(after), set(before))


if __name__ == "__main__":
    unittest.main()
