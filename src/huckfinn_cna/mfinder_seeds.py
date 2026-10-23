"""Per-text mfinder seeds for the final run, derived from one master seed.

seed(text) = numpy SeedSequence([master_seed, text_index]).generate_state(1, uint64)[0]
             mod (seed_max - seed_min + 1) + seed_min
text_index is the 0-based position of the text in [corpus].texts (as in the POS bootstrap), and
seed_min..seed_max is the range the patched mfinder accepts ([mfinder]). The derived seeds are stored in
config [mfinder.final].seeds; a test checks the stored values against this derivation.

Usage:
    uv run python -m huckfinn_cna.mfinder_seeds   # print the seeds block for config/pipeline.toml
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from huckfinn_cna.config import DEFAULT_CONFIG, load_config


def derive_seed(master_seed: int, text_index: int, seed_min: int, seed_max: int) -> int:
    state = int(np.random.SeedSequence([master_seed, text_index]).generate_state(1, dtype=np.uint64)[0])
    return state % (seed_max - seed_min + 1) + seed_min


def derive_seeds(config: dict) -> dict[str, int]:
    m = config["mfinder"]
    return {text: derive_seed(m["final"]["master_seed"], i, m["seed_min"], m["seed_max"])
            for i, text in enumerate(config["corpus"]["texts"])}


def seeds_toml(seeds: dict[str, int]) -> str:
    return "seeds = { " + ", ".join(f"{t} = {s}" for t, s in seeds.items()) + " }\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    print(seeds_toml(derive_seeds(load_config(args.config))), end="")


if __name__ == "__main__":
    main()
