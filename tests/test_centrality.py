"""Tests for huckfinn_cna.centrality. Run: uv run --frozen python -m unittest discover tests"""

import unittest
from collections import Counter

import numpy as np

from huckfinn_cna import centrality as ct
from huckfinn_cna.config import load_config


def path_edges(*words):
    return Counter({(a, b): 1 for a, b in zip(words, words[1:])})


class BetweennessTests(unittest.TestCase):
    def test_path_undirected(self):
        # a - b - c: b lies on the one shortest path a..c; normalized by (n-1)(n-2)/2 = 1
        labels = ["a", "b", "c"]
        bc = ct.betweenness(labels, path_edges("a", "b", "c"), "undirected")
        self.assertEqual(bc.tolist(), [0.0, 1.0, 0.0])

    def test_path_directed(self):
        # a -> b -> c: b on the a->c path only; normalized by (n-1)(n-2) = 2
        bc = ct.betweenness(["a", "b", "c"], path_edges("a", "b", "c"), "directed")
        self.assertEqual(bc.tolist(), [0.0, 0.5, 0.0])

    def test_undirected_collapses_mutual_edges(self):
        edges = Counter({("a", "b"): 1, ("b", "a"): 1, ("b", "c"): 1})
        bc = ct.betweenness(["a", "b", "c"], edges, "undirected")
        self.assertEqual(bc.tolist(), [0.0, 1.0, 0.0])

    def test_weighted_prefers_frequent_route(self):
        # a->b->d (counts 10, 10: distance 0.2) beats a->c->d (counts 1, 1: distance 2)
        edges = Counter({("a", "b"): 10, ("b", "d"): 10, ("a", "c"): 1, ("c", "d"): 1})
        labels = ["a", "b", "c", "d"]
        bc = dict(zip(labels, ct.betweenness(labels, edges, "weighted")))
        self.assertGreater(bc["b"], 0)
        self.assertEqual(bc["c"], 0)
        # unweighted: both routes are shortest, so b and c share
        un = dict(zip(labels, ct.betweenness(labels, edges, "directed")))
        self.assertAlmostEqual(un["b"], un["c"])

    def test_isolated_nodes_count_in_normalization(self):
        bc = ct.betweenness(["a", "b", "c", "z"], path_edges("a", "b", "c"), "undirected")
        self.assertAlmostEqual(bc[1], 1 / 3)       # 1 / ((4-1)(4-2)/2)

    def test_igraph_matches_networkx(self):
        rng = np.random.default_rng(3)
        labels = [f"w{i}" for i in range(60)]
        edges = Counter({(labels[a], labels[b]): 1 for a, b in rng.integers(0, 60, size=(200, 2)) if a != b})
        np.testing.assert_allclose(ct.betweenness(labels, edges, "undirected"), ct.networkx_undirected(labels, edges),
                                   atol=1e-12)


class ShuffleTests(unittest.TestCase):
    def test_shuffle_keeps_lengths_and_tokens(self):
        sents = [["a", "b"], ["c"], ["d", "e", "a"]]
        out = ct.shuffle_sentences(sents, np.random.default_rng(0))
        self.assertEqual([len(s) for s in out], [2, 1, 3])
        self.assertEqual(Counter(w for s in out for w in s), Counter(w for s in sents for w in s))

    def test_shuffle_reproducible(self):
        sents = [list("abcdefg"), list("hij")]
        a = ct.shuffle_sentences(sents, np.random.default_rng([1, 2]))
        b = ct.shuffle_sentences(sents, np.random.default_rng([1, 2]))
        self.assertEqual(a, b)

    def test_adjust(self):
        real = np.array([2.0, 0.0, 1.0])
        shuffled = np.array([[1.0, 0.0, 1.0], [3.0, 0.0, 1.0]])
        mean, sd, ratio, z = ct.adjust(real, shuffled, 1)
        self.assertEqual(mean.tolist(), [2.0, 0.0, 1.0])
        self.assertAlmostEqual(ratio[0], 1.0)
        self.assertTrue(np.isnan(ratio[1]))           # mean 0: undefined
        self.assertAlmostEqual(z[0], 0.0)
        self.assertTrue(np.isnan(z[2]))               # sd 0: undefined

    def test_frequent_mask_includes_ties(self):
        f = np.array([10, 7, 7, 7, 1])
        self.assertEqual(ct.frequent_mask(f, 2).tolist(), [True, True, True, True, False])
        self.assertEqual(ct.frequent_mask(f, 10).tolist(), [True] * 5)


class RankTests(unittest.TestCase):
    def test_competition_ranks(self):
        r = ct.competition_ranks(np.array([0.5, 0.9, 0.5, np.nan, 0.1]))
        self.assertEqual(r[:3].tolist() + [r[4]], [2, 1, 2, 4])
        self.assertTrue(np.isnan(r[3]))

    def test_average_ranks(self):
        self.assertEqual(ct.average_ranks(np.array([3.0, 1.0, 3.0, 2.0])).tolist(), [3.5, 1.0, 3.5, 2.0])

    def test_spearman(self):
        self.assertAlmostEqual(ct.spearman(np.array([1, 2, 3, 4.0]), np.array([10, 20, 30, 40.0])), 1.0)
        self.assertAlmostEqual(ct.spearman(np.array([1, 2, 3, 4.0]), np.array([4, 3, 2, 1.0])), -1.0)

    def test_top_words_excludes_nan_and_breaks_ties(self):
        self.assertEqual(ct.top_words(["b", "a", "c"], np.array([1.0, 1.0, np.nan]), 3), ["a", "b"])

    def test_stability_identical(self):
        labels = list("abcdef")
        s = np.array([6, 5, 4, 3, 2, 1.0])
        rho, n = ct.stability(labels, s, s, 3)
        self.assertAlmostEqual(rho, 1.0)
        self.assertEqual(n, 3)

    def test_heatmap_columns_old_rule(self):
        cfg = {"heatmap_top_n": 2, "heatmap_absent_rank": 1000, "heatmap_max_columns": 3}
        maps = {"A": {"x": 1, "y": 2, "z": 3}, "B": {"y": 1, "z": 2, "x": 5}}
        # union of top-2: x, y, z; mean ranks x 3, y 1.5, z 2.5
        self.assertEqual(ct.heatmap_columns(maps, cfg), ["y", "z", "x"])


class ConfigTests(unittest.TestCase):
    def test_centrality_config(self):
        cfg = load_config()["centrality"]
        self.assertEqual(cfg["primary"], "undirected")
        self.assertNotIn(cfg["master_seed"], (20261022, 20261023, 20261026))
        self.assertEqual(cfg["shuffles"], 30)


if __name__ == "__main__":
    unittest.main()
