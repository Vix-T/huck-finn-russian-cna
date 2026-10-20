"""Tests for scripts/audit_data.py. Run: .venv/bin/python -m unittest discover tests"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import audit_data as a  # noqa: E402


class ReadingTests(unittest.TestCase):
    def test_decode_strips_bom_and_normalizes_newlines(self):
        raw = "﻿Первая\r\nВторая\rТретья\n".encode("utf-8")
        self.assertEqual(a.decode_text(raw), "Первая\nВторая\nТретья\n")

    def test_split_lines_drops_only_trailing_empty(self):
        self.assertEqual(a.split_lines("а\n\nб\n"), ["а", "", "б"])


class BasicsTests(unittest.TestCase):
    def test_line_endings(self):
        counts = a.line_ending_counts(b"a\r\nb\nc\rd")
        self.assertEqual(counts, {"CRLF": 1, "LF": 1, "CR": 1})
        self.assertEqual(a.line_ending_style({"CRLF": 3, "LF": 0, "CR": 0}), "CRLF")
        self.assertTrue(a.line_ending_style(counts).startswith("mixed"))

    def test_invalid_utf8(self):
        self.assertFalse(a.is_valid_utf8(b"\xff\xfe"))
        self.assertTrue(a.is_valid_utf8("ёж".encode("utf-8")))


class TokenTests(unittest.TestCase):
    def test_hyphenated_word_is_one_token_and_digits_excluded(self):
        tokens = a.word_tokens("Кое-что, 25 центов — и Ёж-то! abc")
        self.assertEqual(tokens, ["Кое-что", "центов", "и", "Ёж-то"])

    def test_dangling_hyphen_not_joined(self):
        self.assertEqual(a.word_tokens("кое- что"), ["кое", "что"])

    def test_lengths_paragraphs_are_non_empty_lines(self):
        result = a.lengths("Один абзац.\n\n  \nДругой абзац тут.\n")
        self.assertEqual(result["paragraphs_non_empty_lines"], 2)
        self.assertEqual(result["blank_lines"], 2)
        self.assertEqual(result["line_length_max"], len("Другой абзац тут."))
        self.assertEqual(result["word_tokens"], 5)


class PrereformTests(unittest.TestCase):
    def test_hard_sign_after_consonant(self):
        self.assertEqual(a.hard_sign_after_consonant("какъ-то"), 1)
        self.assertEqual(a.hard_sign_after_consonant("объявил"), 0)

    def test_prereform_counts(self):
        text = "Онъ видѣлъ новаго Іисуса и добрыя дѣла.\nСовременный текст."
        result = a.prereform(text)
        self.assertEqual(result["letter_ѣ"]["count"], 2)
        self.assertEqual(result["letter_і"]["count"], 1)
        self.assertEqual(result["final_hard_sign"]["count"], 2)
        self.assertEqual(result["ending_-аго"]["count"], 1)
        self.assertEqual(result["ending_-ыя"]["count"], 1)
        self.assertEqual(result["letter_ѣ"]["examples"][0]["line"], 1)


class YoAndPunctuationTests(unittest.TestCase):
    def test_yo_usage(self):
        result = a.yo_usage("Всё ещё все. Ёлка")
        self.assertEqual(result["yo_count"], 3)
        self.assertEqual(result["vsyo"], 1)
        self.assertEqual(result["vse"], 1)

    def test_dialogue_lines(self):
        result = a.punctuation("— Кто там?\n  -- Я.\nТекст «цитата».\n")
        self.assertEqual(result["lines_starting_with_dash"]["—"], 1)
        self.assertEqual(result["lines_starting_with_dash"]["-"], 1)
        self.assertEqual(result["counts"]["«"], 1)


class UnusualTests(unittest.TestCase):
    def test_flags_latin_digits_tabs_nbsp(self):
        result = a.unusual_characters("Слово c\t5 ѣ.")
        chars = {r["char"] for r in result["top"]}
        self.assertEqual(chars, {"c", "\t", "5", " "})


class MfinderTests(unittest.TestCase):
    def test_input_audit(self):
        result = a.audit_mfinder_input("1 2 1\n2 3 4\n1 2 1\n3 3 1\n")
        self.assertEqual(result["edges"], 4)
        self.assertEqual(result["duplicate_pairs"], 1)
        self.assertEqual(result["self_loops"], 1)
        self.assertFalse(result["weights_all_one"])
        self.assertEqual(result["weights"]["bins"], {"1": 3, "2": 0, "3-5": 1, "6-10": 0, "11+": 0})
        self.assertEqual(result["weights"]["max"], 4.0)

    def test_compare_figure_to_runs(self):
        figure = {"Ru1": {6: 5.30, 12: 6.22}, "Ru2": {6: 1.00, 12: 2.00}}
        runs = {
            "a": {"Ru1": {6: 5.3, 12: 6.22}, "Ru2": {6: 1.0, 12: 9.99}},
            "b": {"Ru1": {6: 5.3, 12: 5.70}, "Ru2": {6: 1.004, 12: 8.0}},
        }
        result = a.compare_figure_to_runs(figure, runs)
        self.assertEqual(result["cells"], 4)
        self.assertEqual(result["runs"]["a"]["matched"], 3)
        self.assertEqual(result["runs"]["b"]["matched"], 2)
        self.assertEqual([(c["text"], c["motif"]) for c in result["matches_neither"]], [("Ru2", 12)])

    def test_classify_members_before_out(self):
        self.assertEqual(a.classify_mfinder_file("Ru1_mfinder_members_OUT.txt"), "members")
        self.assertEqual(a.classify_mfinder_file("Ru1_mfinder_stats_OUT.txt"), "out")
        self.assertEqual(a.classify_mfinder_file("Ru1_mfinder_input.txt"), "input")
        self.assertEqual(a.mfinder_run("Ru1_mfinder_input_OUT.txt"), "input_OUT")

    def test_out_parsing_and_flags(self):
        content = (
            "\tNum of Nodes: 10 Num of Edges: 20\n"
            "\tNumber of random networks generated : 100\n"
            "Criteria taken : Nreal Zscore > 2.00\n"
            "Full list includes 1 motifs\n"
            "6\t100\t50.0+-5.0\t9.00\t0.000\t3\t1.0\n"  # found-motifs block: ignored
            "Full list of subgraphs size 3 ids:\n"
            "MOTIF\tNREAL\tNRAND\n"
            "6\t100\t50.0+-5.0\t10.00\t0.000\t1.5\t3\n"
            "38\t5\t0.0+-0.0\t888888.00\t0.000\t0.1\t1\n"
            "98\t9\t1.0+-0.1\t-2500.5\t1.000\t0.2\t2\n"
            " (Application total runtime was:  13.66 hours.)\n"
        )
        parsed = a.parse_out(content)
        self.assertEqual({m: r["z"] for m, r in parsed["motifs"].items()}, {6: 10.0, 38: 888888.0, 98: -2500.5})
        self.assertEqual(parsed["motifs"][6]["nrand_std"], 5.0)
        self.assertEqual(parsed["meta"]["random_networks"], "100")
        self.assertEqual(parsed["meta"]["criteria_zscore"], "2.00")
        self.assertEqual(parsed["meta"]["total_runtime"], "13.66 hours")
        flagged = a.flag_zscores({m: r["z"] for m, r in parsed["motifs"].items()})
        self.assertEqual([f["motif"] for f in flagged], [38, 98])
        self.assertTrue(flagged[0]["sentinel"])

    def test_members_parsing(self):
        content = (
            "subgraph id = 6\nNreal : 2\n=====\nPartial list of 3 members:\n\n"
            "1\t2\t3\t\n3\t2\t1\t\n4\t5\t6\t\n"
            "subgraph id = 12\nNreal : 1\n=====\nFull list of 1 members:\n7\t8\t9\t\n"
        )
        self.assertEqual(
            a.parse_members(content),
            {"6": {"header_n": 3, "rows": 3, "unique_node_sets": 2},
             "12": {"header_n": 1, "rows": 1, "unique_node_sets": 1}},
        )
        self.assertTrue(a.at_members_cap({"header_n": 3003, "rows": 3003, "unique_node_sets": 1000}))
        self.assertFalse(a.at_members_cap({"header_n": 3003, "rows": 3003, "unique_node_sets": 1001}))


if __name__ == "__main__":
    unittest.main()
