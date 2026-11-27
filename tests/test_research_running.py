"""ResearchGate.running mirrors the live research thread and nothing else."""

import json
import tempfile
import threading
import unittest

from test_failures import ExplodingBroker, Markets, paper

from warp3c.engine import Engine
from warp3c.live import BUS
from warp3c.operator import SNAPSHOT, publish
from warp3c.state import OrderJournal
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

    def test_status_research_block_is_running_only_while_the_gate_is_busy(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = paper(tmp)
            journal = OrderJournal(cfg.db_path)
            self.addCleanup(journal.close)
            engine = Engine(cfg, ExplodingBroker(), None, journal, Markets())
            gate = ResearchGate()
            inbox = BUS.subscribe()
            self.assertIsNotNone(inbox)
            self.addCleanup(BUS.unsubscribe, inbox)

            def published_running():
                publish(cfg, journal, engine)
                raw = inbox.get(timeout=2)
                text = raw.decode()
                self.assertTrue(text.startswith("event: snapshot\n"))
                payload = json.loads(text.split("data: ", 1)[1])
                block = payload["routes"]["/api/status"]["research"]
                self.assertEqual(block, SNAPSHOT["/api/status"]["research"])
                self.assertIs(block["running"], SNAPSHOT["/api/status"]["research"]["running"])
                return block["running"]

            engine.research_status = gate.status()
            self.assertIs(published_running(), False)

            started = threading.Event()
            release = threading.Event()

            def blocked():
                started.set()
                self.assertTrue(release.wait(2))
                return []

            self.assertTrue(gate.start(blocked))
            self.assertTrue(started.wait(1))
            engine.research_status = gate.status()
            self.assertIs(published_running(), True)
            release.set()
            gate._thread.join(2)
            self.assertFalse(gate._thread.is_alive())
            engine.research_status = gate.status()
            self.assertIs(published_running(), False)


if __name__ == "__main__":
    unittest.main()
