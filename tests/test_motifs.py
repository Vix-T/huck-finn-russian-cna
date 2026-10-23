"""Tests for huckfinn_cna.motifs. Run: uv run python -m unittest discover tests"""

import math
import tempfile
import unittest
from pathlib import Path

from huckfinn_cna import motifs as mo
from huckfinn_cna.config import load_config

CONFIG = load_config()
CFG = CONFIG["motifs"]
CLASSES = CONFIG["networks"]["triads"]

OUT_SAMPLE = """Full list of subgraphs size 3 ids:

MOTIF\tNREAL\tNRAND\t\tNREAL\tNREAL\tCREAL\tUNIQ
ID\t\tSTATS\t\tZSCORE\tPVAL\t[MILI]
6\t3307105\t3298215.1+-1133.5\t7.84\t0.000\t206.00\t942

46\t27449\t24330.4+-300.3\t10.38\t0.000\t1.71\t71
 (Application total runtime was:   3.22 hours.)
"""


class ParseTests(unittest.TestCase):
    def test_read_out(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "x_OUT.txt"
            path.write_text("MOTIF\tNREAL\n46\t27449\t24330.4+-300.3\t10.38\t0.000\t71\t1.71\n\n" + OUT_SAMPLE,
                            encoding="latin-1")
            rows = mo.read_out(path)
        # Only the 'Full list' section is read (the motif list above it has its columns in another order).
        self.assertEqual(sorted(rows), [6, 46])
        self.assertEqual(rows[46], {"n_real": 27449, "mean": 24330.4, "sd": 300.3, "z": 10.38, "pval": 0.0,
                                    "creal": 1.71, "uniq": 71})

    def test_read_mat_r_trailing_tabs(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "x_MAT_R.txt"
            path.write_text("1\t2\t\n3\t4\t\n", encoding="latin-1")
            self.assertEqual(mo.read_mat_r(path), [[1, 2], [3, 4]])

    def test_read_run_log(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "run.log"
            path.write_text("text: Ru1\nrandom networks: 100\nseed: 20261021 (MFINDER_SEED)\n"
                            "input sha256: abc\nelapsed seconds: 5\n", encoding="utf-8")
            log = mo.read_run_log(path)
        self.assertEqual((log["text"], log["r"], log["seed"], log["input_sha256"]), ("Ru1", 100, 20261021, "abc"))


class StatisticsTests(unittest.TestCase):
    def test_mean_sd_sample(self):
        m, sd = mo.mean_sd([1, 2, 3, 4], 1)
        self.assertEqual(m, 2.5)
        self.assertAlmostEqual(sd, math.sqrt(5 / 3))

    def test_empirical_p(self):
        # 2 of 4 random counts >= 5, 3 of 4 <= 5 (ties count on both sides)
        self.assertEqual(mo.empirical_p(5, [1, 5, 9, 3]), (3 / 5, 4 / 5))
        self.assertEqual(mo.empirical_p(100, [1, 2, 3]), (1 / 4, 1.0))

    def test_bh_qvalues(self):
        # Hand-computed: sorted p 0.01, 0.02, 0.03, 0.5 -> 0.04, 0.04, 0.04, 0.5 (monotone step-up)
        self.assertEqual([round(q, 10) for q in mo.bh_qvalues([0.03, 0.5, 0.01, 0.02])], [0.04, 0.5, 0.04, 0.04])
        self.assertEqual(mo.bh_qvalues([1.0, 1.0]), [1.0, 1.0])

    def test_abundance_ratio_and_sp(self):
        self.assertAlmostEqual(mo.abundance_ratio(10, 6, 4), 0.2)
        sp = mo.significance_profile([3.0, -4.0, math.nan])
        self.assertEqual(sp[:2], [0.6, -0.8])
        self.assertTrue(math.isnan(sp[2]))

    def test_z_standard_error(self):
        self.assertAlmostEqual(mo.z_standard_error(0.0, 100), 0.1)
        self.assertAlmostEqual(mo.z_standard_error(10.0, 100), math.sqrt(51) / 10)

    def test_zero_sd_gives_nan_and_flag(self):
        self.assertTrue(math.isnan(mo.z_score(5, 5.0, 0.0)))
        out_row = {"n_real": 3, "mean": 3.0, "sd": 0.0, "z": CFG["sentinel"], "pval": 0, "creal": 0}
        self.assertEqual(mo.triad_flags(3, 3.0, 0.0, out_row, [3, 3], CFG),
                         ["n_real_low", "null_mean_low", "sd_zero", "sentinel"])


def synthetic(n_real_shift: int = 0):
    """_OUT rows and _MAT_R rows for the 13 classes: random counts 100, 102, 104, 106; N_real 103 + shift."""
    ids = sorted(c["mfinder_id"] for c in CLASSES)
    mat = [[100, 102, 104, 106] for _ in ids]
    out = {i: {"n_real": 103 + n_real_shift + k, "mean": 103.0, "sd": 2.58, "z": 0.0, "pval": 0.5, "creal": 1.0,
               "uniq": 1} for k, i in enumerate(ids)}
    return out, mat


class AnalyseTests(unittest.TestCase):
    def test_rows_and_consistency(self):
        out, mat = synthetic()
        rows = mo.analyse(out, mat, CLASSES, CFG)
        self.assertEqual([r["mfinder_id"] for r in rows], [c["mfinder_id"] for c in CLASSES])
        self.assertAlmostEqual(sum(r["concentration"] for r in rows), 1.0)
        self.assertAlmostEqual(sum(r["sp"] ** 2 for r in rows), 1.0)
        first = rows[0]
        self.assertEqual(first["null_mean"], 103.0)
        self.assertAlmostEqual(first["z"], 0.0)
        self.assertEqual(first["flags"], [])

    def test_wrong_ids_or_shapes_fail(self):
        out, mat = synthetic()
        with self.assertRaises(mo.MotifError):
            mo.analyse(out, mat[:-1], CLASSES, CFG)
        out.pop(238)
        with self.assertRaises(mo.MotifError):
            mo.analyse(out, mat, CLASSES, CFG)

    def test_check_mfinder_z(self):
        rows = [{"mfinder_id": 6, "z": 7.8426, "mfinder_z": 7.84}, {"mfinder_id": 12, "z": 8.0, "mfinder_z": 8.32}]
        self.assertEqual(mo.check_mfinder_z(rows, CFG["mfinder_z_tolerance"]), [12])


class OldVsNewTests(unittest.TestCase):
    def test_flips(self):
        rows = [{"mfinder_id": 6, "triad_code": "021D", "z": 3.0, "sp": 0.6},
                {"mfinder_id": 12, "triad_code": "021C", "z": -4.0, "sp": -0.8}]
        cmp = mo.old_vs_new(rows, {6: 1.0, 12: 2.5}, 2.0)
        self.assertEqual([(c["sign_flip"], c["sig_flip"]) for c in cmp], [(False, True), (True, False)])
        self.assertAlmostEqual(cmp[0]["dz"], 2.0)


if __name__ == "__main__":
    unittest.main()
