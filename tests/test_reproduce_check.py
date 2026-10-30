"""Tests for huckfinn_cna.reproduce_check. Run: uv run --frozen python -m unittest discover tests"""

import tempfile
import unittest
from pathlib import Path

from huckfinn_cna import reproduce_check as rc
from huckfinn_cna.config import load_config, repo_path


class ProvenanceTests(unittest.TestCase):
    def test_parses_table_rows_only(self):
        sha = "a" * 64
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "PROVENANCE.md"
            p.write_text(f"# Provenance\n\nSHA-256 is `{'b' * 64}` of nothing.\n\n| File | Label | SHA-256 |\n"
                         f"|---|---|---|\n| Ru1.txt | Ru-1911 | `{sha}` |\n", encoding="utf-8")
            self.assertEqual(rc.provenance_checksums(p), {"Ru1.txt": sha})

    def test_committed_provenance_lists_every_text(self):
        config = load_config()
        self.assertEqual(set(rc.provenance_checksums(repo_path(config, "provenance"))),
                         {f"{t}.txt" for t in config["corpus"]["texts"]})


class NRealTests(unittest.TestCase):
    def test_report_n_real_reads_all_statistics_table_only(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "motif_report.md"
            p.write_text("## Z\n\n| Motif | Triad | Ru1 |\n|---|---|---|\n| 6 | 021D | 8.42* |\n\n## All statistics\n\n"
                         "| Text | Motif | N_real | Null mean |\n|---|---|---|---|\n| Ru1 | 6 | 3,307,088 | 3,298,318.7 |\n\n"
                         "## Next\n\n| Ru2 | 6 | 5 | 1 |\n", encoding="utf-8")
            self.assertEqual(rc.report_n_real(p), {("Ru1", 6): 3307088})

    def test_committed_report_has_every_text_and_triad(self):
        config = load_config()
        got = rc.report_n_real(repo_path(config, "motif_report"))
        self.assertEqual(len(got), len(config["corpus"]["texts"]) * len(config["networks"]["triads"]))


class ConfigTests(unittest.TestCase):
    def test_compared_reports_exist_and_exclude_motif_report(self):
        config = load_config()
        for f in config["reproduce"]["compared_reports"]:
            self.assertTrue((repo_path(config, "raw_dir").parents[1] / f).exists(), f)
        self.assertNotIn(config["paths"]["motif_report"], config["reproduce"]["compared_reports"])


if __name__ == "__main__":
    unittest.main()
