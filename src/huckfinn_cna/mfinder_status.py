"""Progress of the final mfinder runs (data/interim/mfinder/final/TEXT_r{R}/), one line per text.

Random networks done = sections in TEXT_..._INTER.txt (mfinder -oi writes and flushes one after each
random network). Elapsed time runs from the start time in run.log. The estimated finish assumes the
remaining networks take as long on average as those done so far:
    finish = last INTER update + (R - done) * (last INTER update - start) / done
(the real-network count, about a minute, is included in the average; negligible over many networks).

Usage:
    uv run python -m huckfinn_cna.mfinder_status [R]      (default R: [mfinder.final].r)
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path

from huckfinn_cna.config import DEFAULT_CONFIG, load_config, repo_path
from huckfinn_cna.motifs import MotifError, read_run_log

# Header line mfinder writes at the top of every dump of results.
SECTION_MARKER = "MOTIF FINDER RESULTS:"
LOG_TIME = "%Y-%m-%d %H:%M:%S %z"


def networks_done(inter: Path) -> int:
    if not inter.exists():
        return 0
    with inter.open(encoding="latin-1") as fh:
        return sum(1 for line in fh if line.startswith(SECTION_MARKER))


def estimate_finish(start: datetime, last_update: datetime, done: int, r: int) -> datetime | None:
    if done == 0:
        return None
    per_network = (last_update - start) / done
    return last_update + per_network * (r - done)


def hms(delta: timedelta) -> str:
    seconds = int(delta.total_seconds())
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def text_status(run_dir: Path, r: int, now: datetime) -> str:
    log_path = run_dir / "run.log"
    if not log_path.exists():
        return "not started"
    try:
        log = read_run_log(log_path)
    except MotifError:
        return "run.log incomplete"
    start = datetime.strptime(log["start"], LOG_TIME)
    inters = sorted(run_dir.glob("*_INTER.txt"))
    done = networks_done(inters[0]) if inters else 0
    if "exit status" in log:
        end = datetime.strptime(log["end"], LOG_TIME)
        state = "finished" if log["exit status"] == "0" else f"FAILED (exit {log['exit status']})"
        return f"{state}: {done}/{r} random networks, elapsed {hms(end - start)}, ended {end:%a %d %b %H:%M}"
    elapsed = now - start
    if done == 0:
        return f"running: 0/{r} random networks (real network being counted), elapsed {hms(elapsed)}"
    last = datetime.fromtimestamp(inters[0].stat().st_mtime, tz=start.tzinfo)
    finish = estimate_finish(start, last, done, r)
    per = (last - start) / done
    return (f"running: {done}/{r} random networks ({100 * done / r:.1f}%), elapsed {hms(elapsed)}, "
            f"{per.total_seconds() / 60:.2f} min/network, estimated finish {finish:%a %d %b %H:%M}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("r", nargs="?", type=int)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    config = load_config(args.config)
    r = args.r if args.r is not None else config["mfinder"]["final"]["r"]
    final_root = repo_path(config, "mfinder_final_dir")
    now = datetime.now().astimezone()
    print(f"mfinder final runs, R = {r}, at {now:%a %d %b %H:%M:%S}")
    for text in config["corpus"]["texts"]:
        print(f"  {text}: {text_status(final_root / f'{text}_r{r}', r, now)}")


if __name__ == "__main__":
    main()
