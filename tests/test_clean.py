"""Tests for huckfinn_cna.clean. Run: uv run python -m unittest discover tests"""

import hashlib
import tempfile
import unittest
from pathlib import Path

from huckfinn_cna import clean as c
from huckfinn_cna.config import load_config, repo_path

CONFIG = load_config()
CFG = CONFIG["clean"]
CTX = CFG["log_context_chars"]


def apply(fn, text, *args):
    """Run a per-line rule on text; return (new text, changes)."""
    line, changes = c.Line(1, text), []
    fn(line, *args, changes, CTX)
    return line.text, changes


class ReadTests(unittest.TestCase):
    def test_bom_crlf_nfc(self):
        raw = "﻿йод\r\nвторая\r\n".encode("utf-8")  # decomposed й
        changes = []
        lines = c.read_lines(raw, CFG, changes)
        self.assertEqual([ln.text for ln in lines], ["йод", "вторая"])
        self.assertEqual([ln.number for ln in lines], [1, 2])
        self.assertEqual([ch.rule for ch in changes], ["a"])

    def test_already_composed_unchanged(self):
        changes = []
        c.read_lines("ёжик\n".encode("utf-8"), CFG, changes)
        self.assertEqual(changes, [])


class FrontMatterTests(unittest.TestCase):
    BLOCKS = [{"text": "X", "name": "Notice", "first": "НАЧАЛО", "last": "КОНЕЦ БЛОКА"}]

    def result(self, *texts):
        return c.Result(lines=[c.Line(i, t) for i, t in enumerate(texts, start=1)])

    def test_block_removed_by_text(self):
        r = self.result("  НАЧАЛО", "текст уведомления", "КОНЕЦ БЛОКА", "Глава первая.")
        c.remove_front_matter(r, self.BLOCKS, required=True)
        self.assertEqual([ln.text for ln in r.lines], ["Глава первая."])
        self.assertEqual(r.removed_blocks[0]["first_line"], 1)
        self.assertEqual(r.removed_blocks[0]["last_line"], 3)

    def test_missing_block_fails_when_required(self):
        r = self.result("Просто текст.")
        with self.assertRaises(c.CleaningError):
            c.remove_front_matter(r, self.BLOCKS, required=True)

    def test_ambiguous_start_fails(self):
        r = self.result("НАЧАЛО", "КОНЕЦ БЛОКА", "НАЧАЛО")
        with self.assertRaises(c.CleaningError):
            c.remove_front_matter(r, self.BLOCKS, required=True)

    def test_other_text_untouched(self):
        r = self.result("Слово НАЧАЛО в середине строки.")
        c.remove_front_matter(r, self.BLOCKS, required=False)
        self.assertEqual(len(r.lines), 1)


class EditorialNoteTests(unittest.TestCase):
    def run_rule(self, text):
        r = c.Result(lines=[c.Line(1, text)])
        c.remove_editorial_notes(r, CFG["editorial_notes"], CTX)
        return r

    def test_note_with_marker_removed(self):
        r = self.run_rule("аболиционистом [Сторонник отмены рабства. (Прим. изд.)] и станут")
        self.assertEqual(r.lines[0].text, "аболиционистом  и станут")  # rule d collapses the spaces

    def test_other_brackets_kept_and_listed(self):
        r = self.run_rule("слово [пояснение] слово")
        self.assertEqual(r.lines[0].text, "слово [пояснение] слово")
        self.assertEqual(r.brackets_kept[0]["segment"], "[пояснение]")


class WhitespaceTests(unittest.TestCase):
    def test_tabs_runs_and_ends(self):
        text, _ = apply(c.normalize_whitespace, "   —\tНе  болтай ногами!  ")
        self.assertEqual(text, "— Не болтай ногами!")

    def test_clean_line_unchanged(self):
        text, changes = apply(c.normalize_whitespace, "Не болтай ногами!")
        self.assertEqual((text, changes), ("Не болтай ногами!", []))


class LookalikeTests(unittest.TestCase):
    def test_latin_inside_cyrillic_token(self):
        text, _ = apply(c.map_lookalikes, "Признатьcя, оcвобождать", CFG["latin_lookalikes"])
        self.assertEqual(text, "Признаться, освобождать")

    def test_pure_latin_untouched_and_unmappable_left_mixed(self):
        text, _ = apply(c.map_lookalikes, "Maggiore fretta; no-овечьи; XIV", CFG["latin_lookalikes"])
        self.assertEqual(text, "Maggiore fretta; nо-овечьи; XIV")
        self.assertEqual(c.mixed_script_tokens(text), ["nо-овечьи"])


class StandaloneTests(unittest.TestCase):
    def test_ii_to_i(self):
        text, _ = apply(c.replace_standalone, "— II всегда нужно?", CFG["standalone_tokens"])
        self.assertEqual(text, "— И всегда нужно?")

    def test_iii_and_embedded_untouched(self):
        text, _ = apply(c.replace_standalone, "Ричарда III и XIII-II", CFG["standalone_tokens"])
        self.assertEqual(text, "Ричарда III и XIII-II")


class PunctuationTests(unittest.TestCase):
    P = CFG["punctuation"]

    def test_double_hyphen_and_spaced_dashes(self):
        text, _ = apply(c.normalize_punctuation, "-- Кто там? -- Я, -- сказал он - ну – да", self.P)
        self.assertEqual(text, "— Кто там? — Я, — сказал он — ну — да")

    def test_quotes_ellipsis_commas(self):
        text, _ = apply(c.normalize_punctuation, 'Он сказал: "Нет..." и „да“,, потом', self.P)
        self.assertEqual(text, "Он сказал: «Нет…» и «да», потом")

    def test_intraword_hyphens_and_canonical_text_untouched(self):
        line = "Кое-что, из-под, по-моему — «так»… д'Артаньян"
        text, changes = apply(c.normalize_punctuation, line, self.P)
        self.assertEqual((text, changes), (line, []))


class HyphenationTests(unittest.TestCase):
    H = CFG["hyphenation"]

    def test_particles_and_po(self):
        text, _ = apply(c.rejoin_hyphenation, "какая- нибудь, все -таки, По- видимому, по- овечьи", self.H)
        self.assertEqual(text, "какая-нибудь, все-таки, По-видимому, по-овечьи")

    def test_koe_and_iz_pairs_any_spacing(self):
        text, _ = apply(c.rejoin_hyphenation, "кое- что, Кое -как, кое - кто, из- под, Из -за, из - за", self.H)
        self.assertEqual(text, "кое-что, Кое-как, кое-кто, из-под, Из-за, из-за")

    def test_other_breaks_untouched(self):
        line = "из- дома, покое- что, по- лесу, слово- тоже, давным- давно"
        text, changes = apply(c.rejoin_hyphenation, line, self.H)
        self.assertEqual((text, changes), (line, []))

    def test_punctuation_leaves_rejoinable_spaced_hyphens_for_rule_h(self):
        line = c.Line(1, "кое - что, из - под, дом - за, он - то")
        c.normalize_punctuation(line, CFG["punctuation"], [], CTX, c.rejoin_patterns(self.H))
        self.assertEqual(line.text, "кое - что, из - под, дом — за, он - то")

    def test_full_pipeline_rejoins_fully_spaced_variants(self):
        result = c.clean_text("кое - что из - под стола, дом - за рекой\n".encode("utf-8"), "Ru0", CFG)
        self.assertEqual(result.lines[0].text, "кое-что из-под стола, дом — за рекой")


class SplitWordTests(unittest.TestCase):
    def run_rule(self, text):
        line, changes, candidates = c.Line(1, text), [], []
        c.join_split_words(line, changes, candidates, CTX, CFG["split_words"])
        return line.text, changes, candidates

    def test_guard_second_token_capitalized(self):
        text, changes, candidates = self.run_rule("мы оба благо Душествовали")
        self.assertEqual(changes, [])
        self.assertEqual(candidates[0]["reason"], "guard: B capitalized")

    def test_capitalized_first_token_still_joined(self):
        text, _, _ = self.run_rule("Благо душествовали мы")
        self.assertEqual(text, "Благодушествовали мы")

    def test_guard_ne_ni(self):
        self.assertTrue(c.is_known("негодяю") and not c.is_known("годяю"))
        self.assertTrue(c.is_known("ничтожный") and not c.is_known("чтожный"))
        for line, b in (("скряге и не годяю", "годяю"), ("Не годяю", "годяю"), ("ни чтожный", "чтожный")):
            text, changes, candidates = self.run_rule(line)
            self.assertEqual((text, changes), (line, []), line)
            blocked = [x for x in candidates if x["b"] == b]
            self.assertTrue(blocked[0]["reason"].startswith("guard: A is"), line)

    def test_unknown_part_joined(self):
        text, _, _ = self.run_rule("мы оба благо душествовали")
        self.assertEqual(text, "мы оба благодушествовали")

    def test_three_way_split_joined_by_repetition(self):
        self.assertTrue(c.is_known("собственного") and not c.is_known("собст"))
        text, changes, _ = self.run_rule("чувством собст вен ного достоинства")
        self.assertEqual(text, "чувством собственного достоинства")

    def test_both_known_not_joined_but_listed(self):
        text, changes, candidates = self.run_rule("а по том ушел")
        self.assertEqual(text, "а по том ушел")
        self.assertEqual(changes, [])
        self.assertIn(("по", "том", "потом"), [(x["a"], x["b"], x["joined"]) for x in candidates])

    def test_hyphenated_tokens_not_joined(self):
        text, changes, _ = self.run_rule("кое-что душествовали")
        self.assertEqual(changes, [])


class YoTests(unittest.TestCase):
    def test_yo(self):
        text, _ = apply(c.replace_yo, "Всё ещё Ёлка", CFG["yo"])
        self.assertEqual(text, "Все еще Елка")

    def test_no_yo_unchanged(self):
        self.assertEqual(apply(c.replace_yo, "Все еще", CFG["yo"])[1], [])


class ManualCorrectionTests(unittest.TestCase):
    def result(self):
        return c.Result(lines=[c.Line(7, "Гек Фини сказал"), c.Line(8, "слово слово")])

    def test_applied_once(self):
        r = self.result()
        c.apply_manual_corrections(r, [{"text": "Ru2", "line": "7", "before": "Фини", "after": "Финн"}], "Ru2", CTX)
        self.assertEqual(r.lines[0].text, "Гек Финн сказал")

    def test_no_match_or_multiple_matches_fail(self):
        for before, line in (("нет такого", "7"), ("слово", "8")):
            with self.assertRaises(c.CleaningError):
                c.apply_manual_corrections(
                    self.result(), [{"text": "Ru2", "line": line, "before": before, "after": "x"}], "Ru2", CTX)

    def test_other_text_ignored(self):
        r = self.result()
        c.apply_manual_corrections(r, [{"text": "Ru1", "line": "7", "before": "нет", "after": "x"}], "Ru2", CTX)
        self.assertEqual(r.lines[0].text, "Гек Фини сказал")


class AttestedJoinTests(unittest.TestCase):
    """Rule i2 on a synthetic two-text corpus."""

    def corpus(self, text_a, text_b):
        stage1 = {t: c.clean_stage1(s.encode("utf-8"), t, CFG, front_matter_required=False)
                  for t, s in (("A", text_a), ("B", text_b))}
        return c.clean_corpus(stage1, CFG, [], apply_i2=True)

    def test_on_in_config(self):
        self.assertTrue(CFG["attested_joins"]["apply"])

    def test_not_joined_when_a_part_is_one_letter(self):
        results = self.corpus("убил и ушел. в место.\nубили.\nубили.\nвместо.\nвместо.\n", "другой.\n")
        self.assertEqual(results["A"].lines[0].text, "убил и ушел. в место.")

    def test_applied_sample_seeded_and_in_text_order(self):
        joins = [{"line": n} for n in range(100)]
        att = CFG["attested_joins"]
        first, second = c.applied_join_sample(joins, att), c.applied_join_sample(joins, att)
        self.assertEqual(first, second)
        self.assertEqual(len(first), att["applied_sample_size"])
        self.assertEqual(first, sorted(first, key=lambda j: j["line"]))

    def test_joined_when_attested_and_absent_elsewhere(self):
        results = self.corpus("толь ко раз.\nтолько тут.\nтолько там.\n", "другой текст.\n")
        self.assertEqual(results["A"].lines[0].text, "только раз.")
        self.assertEqual(results["A"].attested_joins[0]["attested"], 2)

    def test_not_joined_when_attested_once(self):
        results = self.corpus("толь ко раз.\nтолько тут.\n", "другой текст.\n")
        self.assertEqual(results["A"].lines[0].text, "толь ко раз.")

    def test_not_joined_when_pair_occurs_in_another_text(self):
        results = self.corpus("по том берегу.\nпотом.\nпотом.\n", "шли по том берегу.\n")
        self.assertEqual(results["A"].lines[0].text, "по том берегу.")

    def test_guards_apply(self):
        results = self.corpus("толь Ко раз. не годяю.\nтолько.\nтолько.\nнегодяю.\nнегодяю.\n", "другой.\n")
        self.assertEqual(results["A"].lines[0].text, "толь Ко раз. не годяю.")

    def test_join_does_not_reuse_token(self):
        results = self.corpus("ко ко ко.\nкоко.\nкоко.\n", "другой.\n")
        self.assertEqual(results["A"].lines[0].text, "коко ко.")

    def test_clean_text_never_runs_i2(self):
        with self.assertRaises(c.CleaningError):
            c.clean_stage2(c.Result(lines=[]), "A", CFG, [], None, apply_i2=True)


class ManualFileTests(unittest.TestCase):
    def test_entries_well_formed(self):
        rows = c.read_manual_corrections(repo_path(CONFIG, "manual_corrections"))
        for row in rows:
            self.assertIn(row["text"], CONFIG["corpus"]["texts"])
            self.assertTrue(row["line"].isdigit() and row["before"] and row["reason"])
        # Entries that undo OCR Latin letters must actually contain Latin letters.
        latin = [r for r in rows if r["reason"].startswith("OCR: Latin")]
        self.assertTrue(latin and all(c.LAT_RE.search(r["before"]) for r in latin))


SAMPLE = (
    "﻿   ПРЕДУПРЕЖДЕНИЕ\r\n"
    "-- Кто там?! -- спросил он, \"не\tзная\"... [Пояснение. (Прим. изд.)]\r\n"
    "\r\n"
    " II мы оба благо душествовали; какая- нибудь Признатьcя по- видимому всё,, а по том\r\n"
)


class IdempotenceTests(unittest.TestCase):
    def test_cleaning_twice_changes_nothing(self):
        once = c.clean_text(SAMPLE.encode("utf-8"), "Ru0", CFG)
        twice = c.clean_text(c.render(once.lines).encode("utf-8"), "Ru0", CFG, front_matter_required=False)
        self.assertEqual(c.render(twice.lines), c.render(once.lines))
        self.assertEqual(twice.changes, [])

    def test_line_structure_kept(self):
        result = c.clean_text(SAMPLE.encode("utf-8"), "Ru0", CFG)
        self.assertEqual(len(result.lines), 4)
        self.assertEqual(result.lines[1].text, "— Кто там?! — спросил он, «не зная»…")

    @unittest.skipUnless(repo_path(CONFIG, "clean_dir").exists(), "run the cleaning first")
    def test_real_cleaned_texts_are_fixed_points(self):
        """Re-cleaning changes nothing, except re-doing a rule join that a manual correction undid."""
        manual = c.read_manual_corrections(repo_path(CONFIG, "manual_corrections"))
        for text_id in CONFIG["corpus"]["texts"]:
            undone = {(r["after"], r["before"]) for r in manual if r["text"] == text_id}
            cleaned = (repo_path(CONFIG, "clean_dir") / f"{text_id}.txt").read_bytes()
            again = c.clean_text(cleaned, text_id, CFG, front_matter_required=False)
            self.assertEqual([ch for ch in again.changes if (ch.before, ch.after) not in undone], [], text_id)


@unittest.skipUnless(all((repo_path(CONFIG, "raw_dir") / f"{t}.txt").exists() for t in CONFIG["corpus"]["texts"]),
                     "raw texts not present")
class RawUntouchedTests(unittest.TestCase):
    def test_raw_checksums_unchanged_after_run(self):
        raw_dir = repo_path(CONFIG, "raw_dir")
        digest = lambda: {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(raw_dir.glob("*.txt"))}
        before = digest()
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            c.run(clean_dir=tmp / "clean", review_dir=tmp / "review", report_path=tmp / "report.md")
            self.assertTrue((tmp / "clean" / "Ru1.txt").exists())
        self.assertEqual(digest(), before)


if __name__ == "__main__":
    unittest.main()
