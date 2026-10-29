"""Tests for huckfinn_cna.motif_final. Run: uv run --frozen python -m unittest discover tests"""

import tempfile
import unittest
from pathlib import Path

import numpy as np

from huckfinn_cna import motif_final as mf


class RunTests(unittest.TestCase):
    def test_read_checksums_skips_comments(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.txt"
            p.write_text("# comment\n\nabc  data/interim/networks/Ru1_mfinder.txt\n", encoding="utf-8")
            self.assertEqual(mf.read_checksums(p), {"Ru1_mfinder.txt": "abc"})

    def test_check_run_log(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "run.log"
            p.write_text("start: a\nend: b\nelapsed seconds: 5\nexit status: 0\n", encoding="utf-8")
            self.assertEqual(mf.check_run_log(p)["exit status"], "0")
            p.write_text("start: a\nexit status: 0\n", encoding="utf-8")
            with self.assertRaises(mf.MotifFinalError):
                mf.check_run_log(p)
            p.write_text("start: a\nend: b\nelapsed seconds: 5\nexit status: 1\n", encoding="utf-8")
            with self.assertRaises(mf.MotifFinalError):
                mf.check_run_log(p)

    def test_significant_uses_direction_of_z(self):
        self.assertTrue(mf.significant({"z": 3.0, "q_over": 0.01, "q_under": 1.0}, 0.05))
        self.assertFalse(mf.significant({"z": 3.0, "q_over": 0.2, "q_under": 0.001}, 0.05))
        self.assertTrue(mf.significant({"z": -3.0, "q_over": 1.0, "q_under": 0.01}, 0.05))


class ComparisonTests(unittest.TestCase):
    def test_average_linkage_clades(self):
        corr = np.array([[1, .99, .5, .5], [.99, 1, .5, .5], [.5, .5, 1, .98], [.5, .5, .98, 1]])
        _, clades = mf.average_linkage_clades(corr)
        self.assertEqual(clades, {frozenset({0, 1}), frozenset({2, 3})})

    def test_tree_string(self):
        self.assertEqual(mf.tree_string({frozenset({0, 1})}, ["A", "B", "C"]), "((A, B), C)")

    def test_sp_ranges(self):
        sp = np.array([[0.1, 0.5], [0.3, 0.4]])
        self.assertEqual([i for i, _ in mf.sp_ranges(sp, [6, 12])], [6, 12])
        self.assertAlmostEqual(mf.sp_ranges(sp, [6, 12])[0][1], 0.2)

    def test_stability(self):
        r100 = [{"mfinder_id": 6, "triad_code": "021D", "z": 2.0, "z_se": 0.3, "sp": 0.6},
                {"mfinder_id": 12, "triad_code": "021C", "z": 1.0, "z_se": 0.4, "sp": 0.8}]
        r1000 = [{"mfinder_id": 6, "triad_code": "021D", "z": 2.5, "z_se": 0.4, "sp": 0.9},
                 {"mfinder_id": 12, "triad_code": "021C", "z": 1.0, "z_se": 0.3, "sp": 0.7}]
        st = mf.stability(r100, r1000)
        self.assertAlmostEqual(st["rows"][0]["ratio"], 0.5 / 0.5)
        self.assertAlmostEqual(st["sp_correlation"], -1.0)

    def test_similarity_variants_agree_on_clear_structure(self):
        sp = np.array([[1.0, 0.0, 0.1], [0.9, 0.1, 0.1], [0.0, 1.0, 0.2], [0.1, 0.9, 0.3]])
        for name, (_, clades) in mf.similarity_variants(sp).items():
            self.assertIn(frozenset({0, 1}), clades, name)


if __name__ == "__main__":
    unittest.main()
