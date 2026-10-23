"""Tests for the mfinder cross-check (huckfinn_cna.networks; tools/mfinder). Run: uv run python -m unittest discover tests

The tests that run the binary are skipped until it is built with tools/mfinder/build.sh.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from huckfinn_cna import networks as nw
from huckfinn_cna.config import REPO_ROOT, load_config

CONFIG = load_config()
BINARY = REPO_ROOT / CONFIG["mfinder"]["binary"]
SEED_ENV = CONFIG["mfinder"]["seed_env"]
# The 16-node example network of the mfinder manual, chapter 4.
EXAMPLE = Path(__file__).parent / "data" / "mfinder_manual_example.txt"
# Its 3-node subgraph counts as listed in the manual (all other connected triads: 0).
MANUAL_NREAL = {6: 3, 12: 10, 36: 3, 38: 5}

OUT_SAMPLE = """Full list of subgraphs size 3 ids:

MOTIF\tNREAL\tNRAND\t\tNREAL\tNREAL\tCREAL\tUNIQ
ID\t\tSTATS\t\tZSCORE\tPVAL\t[MILI]
6\t3\t7.4+-0.7\t-6.46\t1.000\t142.86\t1

38\t5\t0.6+-0.7\t6.46\t0.000\t238.10\t4
 (Application total runtime was:    1.0 seconds.)
"""


class ParseTests(unittest.TestCase):
    def test_read_nreal(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "x_OUT.txt"
            path.write_text("header 99 lines\n" + OUT_SAMPLE, encoding="latin-1")
            self.assertEqual(nw.read_mfinder_nreal(path), {6: 3, 38: 5})

    def test_read_input(self):
        n, edges = nw.read_mfinder_input(EXAMPLE)
        self.assertEqual((n, len(edges)), (16, 19))

    def test_exact_counts_match_manual(self):
        n, edges = nw.read_mfinder_input(EXAMPLE)
        counts = nw.checked_triads(n, edges, nw.decode_triad_classes(nw.TRIAD_SIZE))
        self.assertEqual({k: v for k, v in counts.items() if v}, MANUAL_NREAL)


@unittest.skipUnless(BINARY.exists(), "mfinder not built (tools/mfinder/build.sh)")
class BinaryTests(unittest.TestCase):
    def run_mfinder(self, out_dir: str, env_seed: str | None, r: int = 10, flags: list[str] | None = None):
        env = {k: v for k, v in os.environ.items() if k != SEED_ENV}
        if env_seed is not None:
            env[SEED_ENV] = env_seed
        prefix = Path(out_dir) / "example"
        flags = CONFIG["mfinder"]["extra_flags"] if flags is None else flags
        proc = subprocess.run([str(BINARY), str(EXAMPLE), "-s", str(nw.TRIAD_SIZE), "-r", str(r), "-f", str(prefix),
                               *flags], env=env, capture_output=True, text=True)
        return proc, Path(f"{prefix}_OUT.txt"), Path(f"{prefix}_MAT_R.txt")

    def test_fails_without_seed(self):
        with tempfile.TemporaryDirectory() as d:
            proc, _, _ = self.run_mfinder(d, None)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn(SEED_ENV, proc.stderr)

    def test_fails_with_invalid_seed(self):
        with tempfile.TemporaryDirectory() as d:
            for bad in ["", "0", "-5", "12abc", "2147483647"]:
                self.assertNotEqual(self.run_mfinder(d, bad)[0].returncode, 0, bad)

    def test_nreal_matches_manual_and_exact_counts(self):
        with tempfile.TemporaryDirectory() as d:
            proc, out, _ = self.run_mfinder(d, "20261021")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            pairs = nw.compare_nreal(EXAMPLE, out, nw.decode_triad_classes(nw.TRIAD_SIZE))
            self.assertEqual({k: v[0] for k, v in pairs.items() if v[0]}, MANUAL_NREAL)

    def test_same_seed_same_random_counts(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            mat_a = self.run_mfinder(a, "20261021")[2].read_text()
            mat_b = self.run_mfinder(b, "20261021")[2].read_text()
            mat_c = self.run_mfinder(b, "20261022")[2].read_text()
        self.assertEqual(mat_a, mat_b)
        self.assertNotEqual(mat_a, mat_c)

    def test_intermediate_output_changes_no_result(self):
        # -oi (progress for scripts/mfinder_status.sh) must leave _MAT_R and _OUT unchanged, and write one
        # section per random network to _INTER.txt.
        from huckfinn_cna.mfinder_status import networks_done
        flags = CONFIG["mfinder"]["extra_flags"]
        self.assertIn("-oi", flags)
        without = [f for f in flags if f != "-oi"]
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            _, out_a, mat_a = self.run_mfinder(a, "20261023", r=20, flags=without)
            _, out_b, mat_b = self.run_mfinder(b, "20261023", r=20, flags=flags)
            self.assertEqual(mat_a.read_text(), mat_b.read_text())
            strip = lambda p: [ln for ln in p.read_text(encoding="latin-1").splitlines() if "runtime" not in ln]
            self.assertEqual(strip(out_a), strip(out_b))
            self.assertEqual(networks_done(Path(b) / "example_INTER.txt"), 20)
            self.assertFalse((Path(a) / "example_INTER.txt").exists())


if __name__ == "__main__":
    unittest.main()
