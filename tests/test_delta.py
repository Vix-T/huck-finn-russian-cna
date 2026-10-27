"""Tests for huckfinn_cna.delta. Run: uv run --frozen python -m unittest discover tests"""

import unittest
from collections import Counter

import numpy as np

from huckfinn_cna import delta as dl
from huckfinn_cna.config import load_config


class FeatureTests(unittest.TestCase):
    def test_rank_mfw_by_mean_block_relative_frequency(self):
        blocks = [Counter({"a": 1, "b": 3}), Counter({"a": 3, "c": 1})]
        # mean rel. freq over blocks of 4 tokens: a 0.5, b 0.375, c 0.125
        self.assertEqual(dl.rank_mfw(blocks, [4, 4]), ["a", "b", "c"])

    def test_rank_mfw_ties_alphabetical(self):
        self.assertEqual(dl.rank_mfw([Counter({"б": 1, "а": 1})], [2]), ["а", "б"])

    def test_cull_100_keeps_words_in_every_text(self):
        texts = [Counter({"a": 1, "b": 1}), Counter({"a": 2})]
        self.assertEqual(dl.cull(["a", "b"], texts, 100), ["a"])
        self.assertEqual(dl.cull(["a", "b"], texts, 0), ["a", "b"])

    def test_zscore_uses_block_statistics_for_other_units(self):
        blocks = np.array([[1.0], [3.0]])                   # mean 2, sd (ddof 1) sqrt(2)
        zb, zt = dl.zscore(blocks, np.array([[4.0]]), 1)
        np.testing.assert_allclose(zb.ravel(), [-1 / np.sqrt(2), 1 / np.sqrt(2)])
        np.testing.assert_allclose(zt.ravel(), [2 / np.sqrt(2)])

    def test_block_starts_paragraph_aligned(self):
        bounds = np.array([0, 3, 7, 10, 14, 16])
        self.assertEqual(dl.block_starts(bounds, 16, 5), [0, 7])


class DistanceTests(unittest.TestCase):
    def test_cosine_delta(self):
        z = np.array([[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0]])
        d = dl.cosine_delta(z)
        self.assertAlmostEqual(d[0, 1], 1.0)
        self.assertAlmostEqual(d[0, 2], 2.0)
        self.assertEqual(d[0, 0], 0.0)

    def test_classic_delta_is_mean_abs_difference(self):
        z = np.array([[0.0, 0.0], [1.0, -3.0]])
        self.assertAlmostEqual(dl.classic_delta(z)[0, 1], 2.0)

    def test_l2_euclidean(self):
        z = np.array([[2.0, 0.0], [0.0, 5.0]])
        self.assertAlmostEqual(dl.l2_euclidean(z)[0, 1], np.sqrt(2))


class TreeTests(unittest.TestCase):
    def test_clades_from_linkage(self):
        d = np.array([[0, 1, 9, 9], [1, 0, 9, 9], [9, 9, 0, 2], [9, 9, 2, 0]], dtype=float)
        cl = dl.clades(dl.linkage_tree(d, "average"), 4)
        self.assertEqual(cl, {frozenset({0, 1}), frozenset({2, 3})})

    def test_majority_consensus(self):
        a, b, c = frozenset({0, 1}), frozenset({2, 3}), frozenset({1, 2})
        kept, dropped = dl.majority_consensus([{a, b}, {a, b}, {c}], 0.5)
        self.assertEqual({k for k, _ in kept}, {a, b})
        self.assertEqual(dropped, [])

    def test_majority_consensus_drops_conflicts_at_threshold(self):
        a, c = frozenset({0, 1}), frozenset({1, 2})
        kept, dropped = dl.majority_consensus([{a}, {c}], 0.5)
        self.assertEqual(kept, [])
        self.assertEqual({k for k, _ in dropped}, {a, c})

    def test_tree_string(self):
        cl = {frozenset({0, 1}), frozenset({2, 3})}
        self.assertEqual(dl.tree_string(cl, ["A", "B", "C", "D", "E"]), "((A, B), (C, D), E)")

    def test_eder_edges(self):
        d = np.array([[0, 1, 2, 3], [1, 0, 3, 2], [2, 3, 0, 1], [3, 2, 1, 0]], dtype=float)
        e = dl.eder_edges(d, [1.0, 0.5, 0.5])
        self.assertEqual(e[(0, 1)], 2.0)        # mutual nearest neighbours
        self.assertEqual(e[(0, 2)], 1.0)        # runner-up both ways
        self.assertEqual(sum(e.values()), 8.0)  # 4 units x (1 + 0.5 + 0.5)


class BlockTests(unittest.TestCase):
    D = np.array([[0, 1, 5, 5], [1, 0, 5, 5], [5, 5, 0, 1], [5, 5, 1, 0]], dtype=float)

    def test_coherence(self):
        self.assertEqual(dl.coherence(self.D, ["A", "A", "B", "B"]), 1.0)
        self.assertEqual(dl.coherence(self.D, ["A", "B", "A", "B"]), 0.0)

    def test_other_translation_neighbours(self):
        m = dl.other_translation_neighbours(self.D, ["A", "B", "A", "B"], ["A", "B"])
        self.assertEqual(m.tolist(), [[0, 2], [2, 0]])

    def test_relative_positions(self):
        np.testing.assert_allclose(dl.relative_positions(["A", "A", "B"]), [0.25, 0.75, 0.5])

    def test_position_gap(self):
        n, gap, base = dl.position_gap(self.D, ["A", "B", "A", "B"])
        self.assertEqual(n, 4)
        self.assertAlmostEqual(gap, 0.0)       # each block's neighbour is at the same story position
        self.assertAlmostEqual(base, 0.25)


class EmbeddingTests(unittest.TestCase):
    def test_eta_squared(self):
        self.assertAlmostEqual(dl.eta_squared(np.array([0.0, 0.0, 2.0, 2.0]), ["a", "a", "b", "b"]), 1.0)
        self.assertAlmostEqual(dl.eta_squared(np.array([0.0, 2.0, 0.0, 2.0]), ["a", "a", "b", "b"]), 0.0)

    def test_classical_mds_recovers_distances(self):
        pts = np.array([[0.0, 0.0], [3.0, 0.0], [0.0, 4.0]])
        d = np.linalg.norm(pts[:, None] - pts[None], axis=2)
        coords, vals = dl.classical_mds(d)
        np.testing.assert_allclose(np.linalg.norm(coords[:, None] - coords[None], axis=2), d, atol=1e-9)

    def test_disconnected_affinity(self):
        a = np.array([[1, .5, 0], [.5, 1, 0], [0, 0, 1]], dtype=float)
        self.assertEqual(dl.components(a), 2)
        ev = dl.normalized_laplacian_eigenvalues(a)
        self.assertAlmostEqual(ev[0], 0.0)
        self.assertAlmostEqual(ev[1], 0.0)      # one zero eigenvalue per component

    def test_same_layout_up_to_sign(self):
        a = np.array([[1.0, 2.0], [3.0, 4.0]])
        self.assertTrue(dl.same_layout(a, a * np.array([-1, 1])))
        self.assertFalse(dl.same_layout(a, a[::-1]))

    def test_ngram_set(self):
        self.assertEqual(dl.ngram_set(["a", "b", "c"], 2), {("a", "b"), ("b", "c")})


class ConfigTests(unittest.TestCase):
    def test_delta_config(self):
        cfg = load_config()["delta"]
        self.assertEqual(cfg["primary"]["distance"], "cosine")
        self.assertIn(cfg["primary"]["mfw"], cfg["mfw"])
        self.assertEqual(cfg["culling"], [0, 100])


if __name__ == "__main__":
    unittest.main()
