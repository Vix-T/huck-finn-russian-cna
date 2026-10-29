"""Tests for huckfinn_cna.lengthmatched. Run: uv run --frozen python -m unittest discover tests"""

import tempfile
import unittest
from pathlib import Path

from huckfinn_cna import lengthmatched as lm
from huckfinn_cna.config import load_config
from huckfinn_cna.mfinder_seeds import derive_lengthmatched_seeds, derive_seeds

SENTS = [["a", "b", "c"], ["d", "e"], ["f", "g", "h", "i"]]   # 9 words


class SampleTests(unittest.TestCase):
    def test_first_sample_cuts_only_its_last_sentence(self):
        out, b = lm.first_sample(SENTS, 4)
        self.assertEqual(out, [["a", "b", "c"], ["d"]])
        self.assertEqual((b["start_word"], b["end_word"], b["cut_sentence_kept"]), (0, 4, 1))

    def test_last_sample_cuts_only_its_first_sentence(self):
        out, b = lm.last_sample(SENTS, 5)
        self.assertEqual(out, [["e"], ["f", "g", "h", "i"]])
        self.assertEqual((b["start_word"], b["end_word"], b["cut_sentence_kept"]), (4, 9, 1))

    def test_exact_sentence_boundary(self):
        out, _ = lm.first_sample(SENTS, 5)
        self.assertEqual(out, [["a", "b", "c"], ["d", "e"]])

    def test_too_short(self):
        with self.assertRaises(ValueError):
            lm.first_sample(SENTS, 10)

    def test_overlap(self):
        self.assertEqual(lm.overlap({"start_word": 0, "end_word": 6}, {"start_word": 3, "end_word": 9}), 3)
        self.assertEqual(lm.overlap({"start_word": 0, "end_word": 4}, {"start_word": 5, "end_word": 9}), 0)

    def test_sample_edges_stay_within_sentences(self):
        out, _ = lm.last_sample(SENTS, 5)
        res = lm.build_sample(out, [])
        self.assertNotIn(("e", "f"), res["edges"])
        self.assertEqual(res["stats"]["word_tokens"], 5)

    def test_update_checksums_replaces_only_its_section(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.txt"
            p.write_text("# header\nabc  x.txt\n", encoding="utf-8")
            lm.update_checksums(p, ["111  a.txt"])
            lm.update_checksums(p, ["222  a.txt"])
            text = p.read_text(encoding="utf-8")
            self.assertIn("abc  x.txt", text)
            self.assertIn("222  a.txt", text)
            self.assertNotIn("111  a.txt", text)
            self.assertEqual(text.count(lm.SECTION_START), 1)


class SeedTests(unittest.TestCase):
    def test_lengthmatched_seeds_match_config(self):
        config = load_config()
        self.assertEqual(config["mfinder"]["lengthmatched"]["seeds"], derive_lengthmatched_seeds(config))

    def test_lengthmatched_seeds_distinct_from_final(self):
        config = load_config()
        self.assertFalse(set(derive_lengthmatched_seeds(config).values()) & set(derive_seeds(config).values()))


if __name__ == "__main__":
    unittest.main()
