"""Tests for huckfinn_cna.pos_check. Run: uv run python -m unittest discover tests"""

import unittest
from collections import Counter

from huckfinn_cna import pos_check as pc


def row(upos, spacy_upos, word=True, vf="", spacy_vf=""):
    return {"upos": upos, "spacy_upos": spacy_upos, "is_word": "1" if word else "0",
            "verbform": vf, "spacy_verbform": spacy_vf}


class MeasureTests(unittest.TestCase):
    def test_word_rows(self):
        rows = [row("NOUN", "NOUN"), row("PUNCT", "PUNCT", word=False)]
        self.assertEqual(len(pc.word_rows(rows)), 1)

    def test_ranks_ties_share_lower_rank(self):
        r = pc.ranks(Counter({"NOUN": 5, "VERB": 5, "ADJ": 2}))
        self.assertEqual(r, {"NOUN": 1, "VERB": 1, "ADJ": 3})

    def test_top_order_stable_on_ties(self):
        self.assertEqual(pc.top_order(Counter({"VERB": 5, "NOUN": 5, "ADJ": 2}), 2), ["NOUN", "VERB"])

    def test_noun_verb_ratios(self):
        r = pc.noun_verb_ratios(Counter({"NOUN": 6, "VERB": 4, "AUX": 2}))
        self.assertAlmostEqual(r["noun_verb"], 1.5)
        self.assertAlmostEqual(r["noun_verb_aux"], 1.0)
        self.assertIsNone(pc.noun_verb_ratios(Counter({"NOUN": 3}))["noun_verb"])

    def test_verbform_counts(self):
        rows = [row("VERB", "VERB", vf="Fin"), row("AUX", "AUX", vf="Fin"), row("VERB", "VERB", vf="Inf"),
                row("NOUN", "NOUN")]
        self.assertEqual(pc.verbform_counts(rows, "upos", "verbform", {"VERB", "AUX"}), Counter({"Fin": 2, "Inf": 1}))

    def test_agreement(self):
        rows = [row("NOUN", "NOUN"), row("NOUN", "PROPN"), row("VERB", "AUX"), row("VERB", "VERB")]
        rate, pairs = pc.agreement(rows)
        self.assertAlmostEqual(rate, 0.5)
        self.assertEqual(pairs, Counter({("NOUN", "PROPN"): 1, ("VERB", "AUX"): 1}))

    def test_heatmap_tags_summed_over_texts(self):
        tags = pc.heatmap_tags({"A": Counter({"NOUN": 10, "VERB": 1}), "B": Counter({"VERB": 12, "ADJ": 3})}, 2)
        self.assertEqual(tags, ["VERB", "NOUN"])


def para_row(para_id, upos, vf=""):
    return {"para_id": str(para_id), "upos": upos, "verbform": vf}


class BootstrapTests(unittest.TestCase):
    ROWS = [para_row(1, "NOUN"), para_row(1, "VERB", "Fin"), para_row(1, "AUX", "Fin"),
            para_row(2, "NOUN"), para_row(2, "NOUN"), para_row(2, "VERB", "Part"),
            para_row(3, "VERB", "Conv"), para_row(3, "NOUN")]

    def counts(self):
        return pc.paragraph_counts(self.ROWS, 4, "upos", "verbform", {"VERB", "AUX"})

    def test_paragraph_counts(self):
        c = self.counts()
        col = {n: i for i, n in enumerate(pc.COUNT_COLUMNS)}
        self.assertEqual(c.shape, (4, len(pc.COUNT_COLUMNS)))
        self.assertEqual(c[0, col["verbs"]], 2)
        self.assertEqual(c[1, col["NOUN"]], 2)
        self.assertEqual(c[3].sum(), 0)  # paragraph without words

    def test_statistics(self):
        s = pc.statistics(self.counts().sum(axis=0))
        self.assertAlmostEqual(s["noun_verb"], 4 / 3)
        self.assertAlmostEqual(s["noun_verb_aux"], 1.0)
        self.assertAlmostEqual(s["fin"], 2 / 4)
        self.assertAlmostEqual(s["part"], 1 / 4)
        self.assertAlmostEqual(s["conv"], 1 / 4)

    def test_indices_reproducible_and_per_text(self):
        a = pc.bootstrap_indices(50, 20, 7, 0)
        self.assertTrue((a == pc.bootstrap_indices(50, 20, 7, 0)).all())
        self.assertFalse((a == pc.bootstrap_indices(50, 20, 7, 1)).all())
        self.assertEqual(a.shape, (20, 50))
        self.assertTrue(((a >= 0) & (a < 50)).all())

    def test_ci_brackets_point_estimate_on_homogeneous_paragraphs(self):
        import numpy as np
        counts = np.tile(np.array([2, 1, 1, 2, 1, 1, 0]), (30, 1))  # identical paragraphs: zero spread
        ci = pc.bootstrap_ci(counts, pc.bootstrap_indices(30, 200, 1, 0), 0.95)
        point = pc.statistics(counts.sum(axis=0))
        for key, (lo, hi) in ci.items():
            self.assertAlmostEqual(lo, point[key])
            self.assertAlmostEqual(hi, point[key])

    def test_overlap(self):
        self.assertTrue(pc.overlap((0.1, 0.3), (0.3, 0.5)))
        self.assertFalse(pc.overlap((0.1, 0.2), (0.3, 0.5)))
