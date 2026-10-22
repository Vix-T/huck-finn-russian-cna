"""Tests for huckfinn_cna.networks. Run: uv run python -m unittest discover tests"""

import random
import unittest
from collections import Counter

from huckfinn_cna import networks as nw
from huckfinn_cna.config import load_config

CHAR_MAP = {"ё": "е"}


def tok(sent_id, token, lemma=None, word=True, punct=False):
    return {"sent_id": str(sent_id), "token": token, "lemma": lemma if lemma is not None else token.lower(),
            "is_word": "1" if word else "0", "is_punct": "1" if punct else "0"}


def comma(sent_id):
    return tok(sent_id, ",", word=False, punct=True)


# Two sentences: «Том, Гек и Джим ушли.» «Ушли все, всё!» (lemmas given explicitly)
TOY_ROWS = [
    tok(1, "Том", "Том"), comma(1), tok(1, "Гек", "Гек"), tok(1, "и"), tok(1, "Джим", "Джим"),
    tok(1, "ушли", "уйти"), tok(1, ".", word=False, punct=True),
    tok(2, "Ушли", "уйти"), tok(2, "все", "весь"), comma(2), tok(2, "всё", "всё"),
    tok(2, "1911", word=False), tok(2, "все", "все"), tok(2, "!", word=False, punct=True),
]


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.sentences = nw.sentence_lemmas(TOY_ROWS, CHAR_MAP)
        self.edges, self.loops = nw.count_edges(self.sentences)

    def test_known_edges(self):
        self.assertEqual(self.edges, Counter({("том", "гек"): 1, ("гек", "и"): 1, ("и", "джим"): 1,
                                              ("джим", "уйти"): 1, ("уйти", "весь"): 1, ("весь", "все"): 1}))

    def test_no_edges_across_sentences(self):
        # «ушли.» ends sentence 1 and «Ушли» starts sentence 2: both have lemma уйти, so a
        # crossing edge would be the self-loop (уйти, уйти); the last word of sentence 1 links nowhere.
        self.assertNotIn(("уйти", "уйти"), self.loops)
        self.assertFalse(any(a == "уйти" and b != "весь" for a, b in self.edges))

    def test_punctuation_and_non_words_skipped(self):
        self.assertIn(("том", "гек"), self.edges)          # across a comma
        self.assertEqual(self.sentences[1], ["уйти", "весь", "все", "все"])  # «1911» skipped

    def test_yo_mapped_and_self_loop_dropped(self):
        # «всё» -> «все», which is followed by «все»: one self-loop, dropped from the edges
        self.assertEqual(self.loops, Counter({("все", "все"): 1}))
        self.assertNotIn(("все", "все"), self.edges)

    def test_node_ids_and_mfinder_lines(self):
        ids = nw.node_ids(self.sentences)
        self.assertEqual(sorted(ids.values()), list(range(1, len(ids) + 1)))
        id_edges = sorted((ids[a], ids[b]) for a, b in self.edges)
        lines = nw.mfinder_lines(id_edges, 1)
        self.assertEqual(len(lines), 6)
        self.assertTrue(all(line.endswith(" 1") for line in lines))
        with self.assertRaises(nw.NetworkError):
            nw.mfinder_lines(id_edges + id_edges[:1], 1)

    def test_mutual_pairs(self):
        self.assertEqual(nw.mutual_pairs({("a", "b"): 2, ("b", "a"): 1, ("a", "c"): 1}), 1)


class TriadClassTests(unittest.TestCase):
    def setUp(self):
        self.classes = nw.decode_triad_classes(nw.TRIAD_SIZE)

    def test_feed_forward_loop_id(self):
        # mfinder manual §3.3, comment 7: the feed-forward loop 0->1, 0->2, 1->2 has id 38.
        self.assertEqual(nw.mfinder_id([(0, 1), (0, 2), (1, 2)], 3), 38)

    def test_thirteen_connected_classes(self):
        self.assertEqual(len(self.classes), 13)
        self.assertEqual(len({c["mfinder_id"] for c in self.classes}), 13)
        self.assertEqual(len({c["triad_code"] for c in self.classes}), 13)

    def test_config_table_matches_decoded(self):
        self.assertEqual(load_config()["networks"]["triads"], self.classes)


class TriadCountTests(unittest.TestCase):
    def setUp(self):
        self.classes = nw.decode_triad_classes(nw.TRIAD_SIZE)

    def test_known_counts(self):
        # 1->2, 2->3, 1->3 (feed-forward loop) and 3->4: triads {1,2,3} FFL (38), {1,3,4} and
        # {2,3,4} chains (12); {1,2,4} is not connected.
        counts = nw.checked_triads(4, [(1, 2), (2, 3), (1, 3), (3, 4)], self.classes)
        self.assertEqual({k: v for k, v in counts.items() if v}, {38: 1, 12: 2})

    def test_igraph_and_networkx_agree_on_random_graph(self):
        rng = random.Random(20261021)
        n = 30
        edges = sorted({(a, b) for a, b in ((rng.randint(1, n), rng.randint(1, n)) for _ in range(120)) if a != b})
        a = nw.count_triads_igraph(n, edges, self.classes)
        b = nw.count_triads_networkx(n, edges, self.classes)
        self.assertEqual(a, b)
        self.assertGreater(sum(a.values()), 0)


if __name__ == "__main__":
    unittest.main()
