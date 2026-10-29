"""Per-text mfinder seeds for the final run, derived from one master seed.

seed(text) = numpy SeedSequence([master_seed, text_index]).generate_state(1, uint64)[0]
             mod (seed_max - seed_min + 1) + seed_min
text_index is the 0-based position of the text in [corpus].texts (as in the POS bootstrap), and
seed_min..seed_max is the range the patched mfinder accepts ([mfinder]). The derived seeds are stored in
config [mfinder.final].seeds; a test checks the stored values against this derivation.

Length-matched samples ([lengthmatched], [mfinder.lengthmatched]): one seed per TEXT_SAMPLE,
seed = SeedSequence([master_seed, text_index, sample_number]) mapped the same way, sample_number = 1-based
position of the sample in [lengthmatched].samples (1 = first, 2 = last).

Usage:
    uv run python -m huckfinn_cna.mfinder_seeds                  # seeds block for [mfinder.final]
    uv run python -m huckfinn_cna.mfinder_seeds --lengthmatched  # seeds block for [mfinder.lengthmatched]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from huckfinn_cna.config import DEFAULT_CONFIG, load_config


def derive_seed(master_seed: int, text_index: int, seed_min: int, seed_max: int, sample_number: int | None = None) -> int:
    entropy = [master_seed, text_index] if sample_number is None else [master_seed, text_index, sample_number]
    state = int(np.random.SeedSequence(entropy).generate_state(1, dtype=np.uint64)[0])
    return state % (seed_max - seed_min + 1) + seed_min


def derive_seeds(config: dict) -> dict[str, int]:
    m = config["mfinder"]
    return {text: derive_seed(m["final"]["master_seed"], i, m["seed_min"], m["seed_max"])
            for i, text in enumerate(config["corpus"]["texts"])}


def derive_lengthmatched_seeds(config: dict) -> dict[str, int]:
    m, lm, texts = config["mfinder"], config["lengthmatched"], config["corpus"]["texts"]
    return {f"{t}_{sample}": derive_seed(m["lengthmatched"]["master_seed"], texts.index(t), m["seed_min"], m["seed_max"], k)
            for t in lm["texts"] for k, sample in enumerate(lm["samples"], start=1)}


def seeds_toml(seeds: dict[str, int]) -> str:
    return "seeds = { " + ", ".join(f"{t} = {s}" for t, s in seeds.items()) + " }\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--lengthmatched", action="store_true", help="seeds for the length-matched samples")
    args = parser.parse_args()
    config = load_config(args.config)
    print(seeds_toml(derive_lengthmatched_seeds(config) if args.lengthmatched else derive_seeds(config)), end="")


if __name__ == "__main__":
    main()
