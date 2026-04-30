#!/usr/bin/env python3
"""Generate committee statistics for the ReproDB website (CLI orchestrator).

Thin wrapper around the :mod:`src.generators.committee_stats` package, which
splits the work into three submodules:

- ``committee_stats.scraping``       — fetch & clean committee data
- ``committee_stats.classification`` — country / continent / institution
- ``committee_stats.charting``       — matplotlib chart generation
"""

from __future__ import annotations

import argparse
import logging

from src.generators.committee_stats import generate_committee_data

logger = logging.getLogger(__name__)

__all__ = ["generate_committee_data", "main"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate committee statistics for the research artifacts website")
    parser.add_argument(
        "--conf_regex",
        type=str,
        default=".*20[12][0-9]",
        help="Regular expression for conference names/years",
    )
    parser.add_argument(
        "--output_dir",
        type=str,
        default=None,
        help="Output directory (website root, e.g. ../reprodb.github.io/src)",
    )
    args = parser.parse_args()

    generate_committee_data(args.conf_regex, args.output_dir)


if __name__ == "__main__":
    from src.utils.io.logging_config import setup_logging

    setup_logging()

    main()
