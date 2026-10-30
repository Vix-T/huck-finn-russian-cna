"""Tests for huckfinn_cna.lexical. Run: uv run --frozen python -m unittest discover tests"""

import math
import unittest

import numpy as np

from huckfinn_cna import lexical as lx
from huckfinn_cna.config import load_config


def codes(text: str) -> np.ndarray:
    """Toy tokens: one character per token, coded a=0, b=1, ..."""
    return np.array([ord(ch) - ord("a") for ch in text], dtype=np.int64)


def naive_mattr(seq, w):
    return sum(len(set(seq[s:s + w])) / w for s in range(len(seq) - w + 1)) / (len(seq) - w + 1)


class TokenTests(unittest.TestCase):
    def test_lemma_key_lowercases_and_maps_yo(self):
        self.assertEqual(lx.lemma_key("Всё", {"ё": "е"}), "все")

    def test_fused_key_deletes_hyphen(self):
        self.assertEqual(lx.fused_key("Кое-что", {"ё": "е"}), "коечто")

    def test_encode_bounds(self):
        v = lx.encode(["x", "y", "x", "z"], [0, 0, 2, 2], 4)
        self.assertEqual(v.codes.tolist(), [0, 1, 0, 2])
        self.assertEqual(v.bounds.tolist(), [0, 2, 2, 4, 4])
        self.assertEqual(v.types, ["x", "y", "z"])


class MeasureTests(unittest.TestCase):
    def test_msttr_drops_short_final_chunk(self):
        # chunks abac (3/4), aabb (2/4), c (1 < 4 * 0.5: dropped)
        self.assertAlmostEqual(lx.msttr(codes("abacaabbc"), 4, 0.5), 0.625)

    def test_msttr_keeps_half_chunk(self):
        # chunks abac (3/4), aabb (2/4), cc (2 = 4 * 0.5: kept, 1/2)
        self.assertAlmostEqual(lx.msttr(codes("abacaabbcc"), 4, 0.5), (0.75 + 0.5 + 0.5) / 3)

    def test_msttr_single_short_chunk_kept(self):
        self.assertAlmostEqual(lx.msttr(codes("ab"), 4, 0.5), 1.0)

    def test_mattr_known_values(self):
        self.assertAlmostEqual(lx.mattr(codes("abab"), 2), 1.0)
        self.assertAlmostEqual(lx.mattr(codes("aab"), 2), 0.75)
        self.assertAlmostEqual(lx.mattr(codes("aaaa"), 4), 0.25)

    def test_mattr_matches_naive(self):
        rng = np.random.default_rng(1)
        seq = rng.integers(0, 15, size=300)
        for w in (1, 7, 50, 300):
            self.assertAlmostEqual(lx.mattr(seq, w), naive_mattr(seq.tolist(), w), places=12)

    def test_mattr_window_too_long(self):
        with self.assertRaises(ValueError):
            lx.mattr(codes("ab"), 3)

    def test_previous_occurrence(self):
        self.assertEqual(lx.previous_occurrence(codes("abab")).tolist(), [-1, -1, 0, 1])

    def test_mtld_pass_known_value(self):
        # "abaab", threshold 0.72: a 1, b 1, a 2/3 <= 0.72 -> factor; a 1, b 1 -> partial (1 - 1)/0.28 = 0.
        self.assertAlmostEqual(lx.mtld_pass([0, 1, 0, 0, 1], 0.72, 2), 5.0)
        # "abcab": a, b, c TTR 1; a 3/4; b 3/5 = 0.6 -> one factor; nothing left -> 5 / 1.
        self.assertAlmostEqual(lx.mtld_pass([0, 1, 2, 0, 1], 0.72, 3), 5.0)
        # "abca": TTR ends at 3/4 -> partial factor 0.25/0.28.
        self.assertAlmostEqual(lx.mtld_pass([0, 1, 2, 0], 0.72, 3), 4 / (0.25 / 0.28))

    def test_mtld_all_unique_is_length(self):
        self.assertAlmostEqual(lx.mtld(codes("abcd"), 0.72), 4.0)

    def test_mtld_is_mean_of_both_directions(self):
        seq = codes("abcaabdc")
        expected = (lx.mtld_pass(seq.tolist(), 0.72, 4) + lx.mtld_pass(seq.tolist()[::-1], 0.72, 4)) / 2
        self.assertAlmostEqual(lx.mtld(seq, 0.72), expected)

    def test_hdd_known_value(self):
        # tokens a a b, 2 draws: P0(a) = 0 -> 1/2; P0(b) = C(2,2)/C(3,2) = 1/3 -> (2/3)/2.
        self.assertAlmostEqual(lx.hdd(np.array([2, 1]), 3, 2), 0.5 + 1 / 3)

    def test_hdd_equals_exact_arithmetic(self):
        from fractions import Fraction
        freqs, n, draws = [7, 3, 3, 1, 1, 1, 20], 36, 10
        exact = Fraction(0)
        for fr in freqs:
            p0 = Fraction(1)
            for k in range(draws):
                p0 *= Fraction(max(n - fr - k, 0), n - k)
            exact += (1 - p0) / draws
        self.assertAlmostEqual(lx.hdd(np.array(freqs), n, draws), float(exact), places=14)

    def test_yule_k_known_value(self):
        # a a b: N = 3, sum f^2 = 5 -> 10^4 * 2 / 9
        self.assertAlmostEqual(lx.yule_k(np.array([2, 1]), 3, 10000), 20000 / 9)

    def test_honore_r_known_value(self):
        # a a b c: N = 4, V = 3, V1 = 2 -> 100 ln 4 / (1/3)
        self.assertAlmostEqual(lx.honore_r(np.array([2, 1, 1]), 4, 100), 300 * math.log(4))
        self.assertTrue(math.isnan(lx.honore_r(np.array([1, 1]), 2, 100)))

    def test_entropy_known_values(self):
        self.assertAlmostEqual(lx.entropy(np.array([1, 1]), 2, 2), 1.0)
        self.assertAlmostEqual(lx.entropy(np.array([2, 2, 2, 2]), 8, 2), 2.0)

    def test_sentence_measures(self):
        words = np.array([3, 0, 5, 10, 2])
        m = lx.sentence_measures(words, punct_total=20, method="linear")
        self.assertAlmostEqual(m["mean"], 5.0)          # sentence with no word excluded
        self.assertAlmostEqual(m["median"], 4.0)
        self.assertAlmostEqual(m["iqr"], 6.25 - 2.75)
        self.assertAlmostEqual(m["punct_per_1000"], 1000.0)


class CrossCheckTests(unittest.TestCase):
    def test_agrees_with_lexicalrichness(self):
        from lexicalrichness import LexicalRichness
        rng = np.random.default_rng(2)
        seq = rng.zipf(1.6, size=3000) % 400
        words = [f"w{x}" for x in seq]
        lex = LexicalRichness(words, preprocessor=None, tokenizer=None)
        view = lx.encode(words, [0] * len(words), 1)
        freqs = lx.frequencies(view.codes)
        self.assertAlmostEqual(lx.mtld(view.codes, 0.72), lex.mtld(threshold=0.72), places=9)
        self.assertAlmostEqual(lx.hdd(freqs, len(words), 42), lex.hdd(draws=42), places=9)
        for w in (100, 500):
            self.assertAlmostEqual(lx.mattr(view.codes, w), lex.mattr(window_size=w), places=9)


class SampleTests(unittest.TestCase):
    def test_sample_length(self):
        self.assertEqual(lx.sample_length([48182, 63176], 1000), 48000)

    def test_paragraph_starts_skip_empty(self):
        self.assertEqual(lx.paragraph_starts(np.array([0, 3, 3, 7, 10])).tolist(), [0, 3, 7])

    def test_nonoverlapping_starts(self):
        starts = np.array([0, 3, 7, 10, 14])
        # N = 5: [0, 5) -> next start >= 5 is 7: [7, 12) -> next >= 12 is 14: 14 + 5 > 16, stop
        self.assertEqual(lx.nonoverlapping_starts(starts, 16, 5), [0, 7])

    def test_random_starts_leave_enough_tokens(self):
        starts = np.array([0, 3, 7, 10, 14])
        r = lx.random_starts(starts, 16, 5, 100, np.random.default_rng(0))
        self.assertTrue(set(r.tolist()) <= {0, 3, 7, 10})

    def test_random_starts_reproducible(self):
        cfg = {"master_seed": 5}
        a = lx.random_starts(np.arange(50), 60, 5, 10, lx.rng_for(cfg, 1, 2))
        b = lx.random_starts(np.arange(50), 60, 5, 10, lx.rng_for(cfg, 1, 2))
        self.assertEqual(a.tolist(), b.tolist())

    def test_gather_in_drawn_order(self):
        bounds = np.array([0, 2, 2, 5])
        self.assertEqual(lx.gather(bounds, np.array([2, 0, 1, 2])).tolist(), [2, 3, 4, 0, 1, 2, 3, 4])

    def test_richness_order(self):
        self.assertEqual(lx.richness_order({"a": 1, "b": 3}, True), ["b", "a"])
        self.assertEqual(lx.richness_order({"a": 1, "b": 3}, False), ["a", "b"])

    def test_half_length_summary_and_overlaps(self):
        cfg = {"bootstrap_ci": 0.95, "quantile_method": "linear"}
        results = {"A": {"views": {"all": {"half": [{"k": x} for x in np.linspace(0, 1, 101)]}}},
                   "B": {"views": {"all": {"half": [{"k": x} for x in np.linspace(0.9, 2, 101)]}}},
                   "C": {"views": {"all": {"half": [{"k": x} for x in np.linspace(5, 6, 101)]}}}}
        s = lx.summarize_half(results["A"]["views"]["all"], "k", cfg)
        self.assertAlmostEqual(s["mean"], 0.5)
        self.assertAlmostEqual(s["lo"], 0.025)
        self.assertEqual(lx.half_overlaps(results, "all", "k", cfg), [("A", "B")])


class ConfigTests(unittest.TestCase):
    def test_lexical_config(self):
        cfg = load_config()["lexical"]
        self.assertEqual(cfg["primary_view"], "all")
        self.assertIn(cfg["mattr_primary"], cfg["mattr_windows"])
        self.assertNotIn(cfg["master_seed"], (20261022, 20261023))
        self.assertNotEqual(cfg["bootstrap_stream"], cfg["windows_stream"])
        self.assertEqual(len({cfg["bootstrap_stream"], cfg["windows_stream"], cfg["half_windows_stream"]}), 3)


if __name__ == "__main__":
    unittest.main()
