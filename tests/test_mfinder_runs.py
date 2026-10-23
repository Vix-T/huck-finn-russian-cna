"""Tests for the final-run seeds and progress (huckfinn_cna.mfinder_seeds, huckfinn_cna.mfinder_status).
Run: uv run python -m unittest discover tests"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from huckfinn_cna import mfinder_seeds as ms
from huckfinn_cna import mfinder_status as st
from huckfinn_cna.config import load_config

CONFIG = load_config()


class SeedTests(unittest.TestCase):
    def test_config_seeds_equal_derivation(self):
        self.assertEqual(CONFIG["mfinder"]["final"]["seeds"], ms.derive_seeds(CONFIG))

    def test_seeds_in_range_and_distinct(self):
        m = CONFIG["mfinder"]
        seeds = ms.derive_seeds(CONFIG)
        self.assertEqual(list(seeds), CONFIG["corpus"]["texts"])
        self.assertEqual(len(set(seeds.values())), len(seeds))
        self.assertTrue(all(m["seed_min"] <= s <= m["seed_max"] for s in seeds.values()))

    def test_master_seed_separate_from_bootstrap(self):
        self.assertNotEqual(CONFIG["mfinder"]["final"]["master_seed"], CONFIG["pos_check"]["bootstrap_seed"])

    def test_derive_seed_bounds(self):
        self.assertEqual(ms.derive_seed(1, 0, 5, 5), 5)
        self.assertEqual(ms.derive_seed(20261023, 0, 1, 2147483646), ms.derive_seed(20261023, 0, 1, 2147483646))
        self.assertNotEqual(ms.derive_seed(20261023, 0, 1, 2147483646), ms.derive_seed(20261023, 1, 1, 2147483646))


TZ = timezone(timedelta(hours=-7))
LOG_START = "text: Ru1\nrandom networks: 10\nseed: 7 (MFINDER_SEED)\ninput sha256: abc\nstart: 2026-10-23 19:30:00 -0700\n"


class StatusTests(unittest.TestCase):
    def test_estimate_finish(self):
        start = datetime(2026, 10, 23, 19, 30, tzinfo=TZ)
        last = start + timedelta(hours=2)
        self.assertEqual(st.estimate_finish(start, last, 4, 10), last + timedelta(hours=3))
        self.assertIsNone(st.estimate_finish(start, last, 0, 10))

    def test_text_status(self):
        now = datetime(2026, 10, 23, 21, 0, tzinfo=TZ)
        with tempfile.TemporaryDirectory() as d:
            run = Path(d) / "Ru1_r10"
            self.assertEqual(st.text_status(run, 10, now), "not started")
            run.mkdir()
            (run / "run.log").write_text(LOG_START, encoding="utf-8")
            self.assertIn("running: 0/10", st.text_status(run, 10, now))
            inter = run / "Ru1_r10_s7_INTER.txt"
            inter.write_text(f"{st.SECTION_MARKER}\nx\n{st.SECTION_MARKER}\n", encoding="latin-1")
            last = datetime(2026, 10, 23, 20, 30, tzinfo=TZ)   # 2 networks in 1 hour -> 8 more by 00:30
            os.utime(inter, (last.timestamp(), last.timestamp()))
            status = st.text_status(run, 10, now)
            self.assertIn("running: 2/10", status)
            self.assertIn("30.00 min/network", status)
            self.assertIn("Sat 24 Oct 00:30", status)
            (run / "run.log").write_text(LOG_START + "end: 2026-10-23 22:00:00 -0700\nelapsed seconds: 9000\n"
                                         "exit status: 0\n", encoding="utf-8")
            self.assertTrue(st.text_status(run, 10, now).startswith("finished: 2/10"))


if __name__ == "__main__":
    unittest.main()
