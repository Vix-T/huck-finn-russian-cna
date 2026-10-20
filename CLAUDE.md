# Huck Finn Russian Translations — Stylometry & Complex Network Analysis

Rebuild of a study comparing five Russian translations of Mark Twain's
*Adventures of Huckleberry Finn* using classic stylometry and complex
network analysis (word-adjacency networks, 3-node motifs, betweenness).

## Corpus (data/raw/)
| File    | Label                    | Notes            |
|---------|--------------------------|------------------|
| Ru1.txt | Ru-1911-Engelgardt       | full             |
| Ru2.txt | Ru-1926-Anon             | abridged         |
| Ru3.txt | Ru-1933-Chukovsky-(Ed.)  | abridged         |
| Ru4.txt | Ru-1949-Braude           | full             |
| Ru5.txt | Ru-1960-Daruzes          | full             |

## Ground rules
- The old repo at /Users/vixtalbot/Code/nlp-descriptive-russian-translations
  (tag report-v3) is READ-ONLY reference. Never edit, move, or delete anything there.
- data/raw/ is read-only. Never modify raw texts. Derived files go elsewhere.
- METHODS.md (added later) is the source of truth for methods. If a decision
  isn't covered by it, stop and ask. Never choose a method silently.
- Work one stage at a time. Do only the task you were given; don't start the next.
- Use the project .venv only, managed by uv (pyproject.toml + uv.lock; `uv add`,
  `uv run`). Never install packages globally. Keep uv.lock current.
- All parameters live in one config file once it exists. No magic numbers in code.
- Seed all randomness and record the seeds.
- Never commit raw texts. Commit code, configs, tests and docs.
- Write small, testable functions; tests go in tests/.
- At the end of each stage, append that stage's section to docs/amendment/amendment_report.md
  and update the Executive Summary table.
- Commit at the end of each task with a clear message. Do not push unless asked.
- When done, report: what you did, what you found, anything surprising or
  uncertain.

## Language notes
Texts are Russian, UTF-8. When reading data/raw/, ё, hyphenated words, dashes and
punctuation are preserved exactly; only the cleaning stage changes them, by the
rules in docs/decisions.md. The cleaned texts in data/interim/clean/ (produced by
`uv run python -m huckfinn_cna.clean`) are the source for every later stage.
Sentence splitting happens on original-case text.
