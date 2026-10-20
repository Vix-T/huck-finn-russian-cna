"""Tests for huckfinn_cna.tag. Run: uv run python -m unittest discover tests

Tagger tests need the Stanza models in models/stanza (uv run python -m huckfinn_cna.tag --download)
and are skipped without them.
"""

import tempfile
import unittest
from pathlib import Path

from huckfinn_cna import tag as t
from huckfinn_cna.config import load_config, repo_path

CONFIG = load_config()
CFG = CONFIG["tag"]
MODELS_PRESENT = (repo_path(CONFIG, "stanza_model_dir") / "resources.json").exists()

SAMPLE = [
    "— Что-то кое-как, по-моему, из-за него! Он был добр… «Нет», — сказал Том.",
    "Ну-ка, Гек, иди-ка сюда; мы пошли домой.",
    "Взгляни-ка, мало-помалу он-то и пришел. Наконец-то! Да-с, Матильда-Анжелина-Араминта.",
]


def tok(text, start, end, upos="X"):
    return t.Token(text, start, end, 1, 1, text, upos, "")


class HelperTests(unittest.TestCase):
    def test_is_word(self):
        for w in ("что-то", "Гек", "кое-как", "Maggiore"):
            self.assertTrue(t.is_word(w), w)
        for w in ("—", ",", "500", "т.", "-то", "что-"):
            self.assertFalse(t.is_word(w), w)

    def test_is_punct(self):
        for p in ("—", "«", "…", "!", "?!"):
            self.assertTrue(t.is_punct(p), p)
        for p in ("а", "500", "т.", ""):
            self.assertFalse(t.is_punct(p), p)

    def test_verbform(self):
        self.assertEqual(t.verbform("Aspect=Perf|Mood=Ind|VerbForm=Fin|Voice=Act"), "Fin")
        self.assertEqual(t.verbform("Case=Nom"), "")
        self.assertEqual(t.verbform(""), "")

    def test_check_characters_passes_and_fails(self):
        p = "Он, да."
        good = [tok("Он", 0, 2), tok(",", 2, 3), tok("да", 4, 6), tok(".", 6, 7)]
        t.check_characters(p, good)
        with self.assertRaises(t.TaggingError):
            t.check_characters(p, good[:-1])  # character lost
        with self.assertRaises(t.TaggingError):
            t.check_characters(p, [tok("Он", 0, 2), tok(",", 2, 3), tok("до", 4, 6), tok(".", 6, 7)])

    def test_split_hyphenated_detected(self):
        p = "что-то есть"
        self.assertEqual(t.split_hyphenated(p, [tok("что-то", 0, 6), tok("есть", 7, 11)]), [])
        self.assertEqual(t.split_hyphenated(p, [tok("что", 0, 3), tok("-то", 3, 6), tok("есть", 7, 11)]), ["что-то"])

    def test_dangling_hyphen_is_not_a_hyphenated_word(self):
        self.assertEqual(t.HYPHENATED_RE.findall("Сара-Мэри-Алексан- дер и черные-пре- черные, кто-то"), ["кто-то"])

    def test_merge_hyphenated_pieces(self):
        p = "Взгляни-ка сюда. Да-с."
        # Взгляни / - / ка | сюда | . || Да / - / с | .
        sents = [[(0, 7), (7, 8), (8, 10), (11, 15), (15, 16)], [(17, 19), (19, 20), (20, 21), (21, 22)]]
        merged = t.merge_hyphenated(p, sents)
        self.assertEqual([[p[s:e] for s, e in sent] for sent in merged], [["Взгляни-ка", "сюда", "."], ["Да-с", "."]])

    def test_merge_across_sentence_boundary_keeps_first_sentence(self):
        p = "Ну-ка"
        merged = t.merge_hyphenated(p, [[(0, 2), (2, 3)], [(3, 5)]])
        self.assertEqual(merged, [[(0, 5)]])

    def test_merge_leaves_dashes_and_plain_tokens(self):
        p = "Он — там - тут"
        sents = [[(0, 2), (3, 4), (5, 8), (9, 10), (11, 14)]]
        self.assertEqual(t.merge_hyphenated(p, sents), sents)

    def test_spaces_after(self):
        p = "Он, да."
        toks = [tok("Он", 0, 2), tok(",", 2, 3), tok("да", 4, 6), tok(".", 6, 7)]
        self.assertEqual(t.spaces_after(toks, p), [False, True, False, False])

    def test_cache_key_changes_with_file_and_config(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "Ru9.txt"
            path.write_text("Он пошел.\n", encoding="utf-8")
            k1 = t.cache_key(path, CFG)
            self.assertEqual(k1, t.cache_key(path, dict(CFG)))
            self.assertNotEqual(k1, t.cache_key(path, CFG | {"content_upos": ["NOUN"]}))
            path.write_text("Он пошел!\n", encoding="utf-8")
            self.assertNotEqual(k1, t.cache_key(path, CFG))

    def test_columns(self):
        rows = t.token_rows("Ru9", [[tok("Он", 0, 2, "PRON")]], set(CFG["content_upos"]))
        self.assertEqual(len(rows[0]), len(t.COLUMNS))
        for col in ("text", "para_id", "sent_id", "tok_id", "token", "lemma", "upos", "feats", "verbform",
                    "is_word", "is_punct", "is_content", "spacy_upos", "spacy_lemma"):
            self.assertIn(col, t.COLUMNS)

    def test_tsv_round_trip_with_quote_lemma(self):
        token = tok("«", 0, 1, "PUNCT")
        token.lemma = '"'
        rows = t.token_rows("Ru9", [[token]], set(CFG["content_upos"]))
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "Ru9.tsv"
            t.write_tagged(path, rows)
            back = t.read_tagged(path)
        self.assertEqual(list(back[0]), t.COLUMNS)
        self.assertEqual((back[0]["token"], back[0]["lemma"]), ("«", '"'))


@unittest.skipUnless(MODELS_PRESENT, "Stanza models not downloaded")
class TaggerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stanza = t.load_stanza(CFG, repo_path(CONFIG, "stanza_model_dir"))
        cls.spacy = t.load_spacy(CFG)
        cls.tagged, cls.split = t.tag_text(SAMPLE, cls.stanza, cls.spacy, CFG)

    def test_integrity_on_sample(self):
        for p, toks in zip(SAMPLE, self.tagged):
            t.check_characters(p, toks)

    def test_hyphenated_words_single(self):
        self.assertEqual(self.split, [])
        tokens = [x.text for toks in self.tagged for x in toks]
        for w in ("Что-то", "кое-как", "по-моему", "из-за", "Ну-ка", "иди-ка", "Взгляни-ка", "мало-помалу",
                  "он-то", "Наконец-то", "Да-с", "Матильда-Анжелина-Араминта"):
            self.assertIn(w, tokens)

    def test_spacy_receives_stanza_tokens(self):
        from spacy.tokens import Doc
        for p, toks in zip(SAMPLE, self.tagged):
            doc = self.spacy(Doc(self.spacy.vocab, words=[x.text for x in toks], spaces=t.spaces_after(toks, p)))
            self.assertEqual([x.text for x in doc], [x.text for x in toks])
            self.assertEqual(doc.text, p)
        self.assertTrue(all(x.spacy_upos for toks in self.tagged for x in toks))

    def test_sentence_ids_continue_across_paragraphs(self):
        first, second = self.tagged[:2]
        self.assertEqual(second[0].sent_id, max(x.sent_id for x in first) + 1)
