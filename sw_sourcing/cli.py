"""Cron entrypoint: `python -m sw_sourcing.cli scan [--source NAME]`."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from sw_sourcing.adapters.base import Adapter
from sw_sourcing.adapters.ebay import EbayAdapter
from sw_sourcing.adapters.facebook_assist import FacebookAssistAdapter
from sw_sourcing.alerts.discord import DiscordAlerts
from sw_sourcing.core.dedupe import Dedupe
from sw_sourcing.core.vision import ClaudeCliVisionClient, Vision
from sw_sourcing.pipeline import Pipeline
from sw_sourcing.storage.config import Config
from sw_sourcing.storage.db import Database

logger = logging.getLogger(__name__)

_DB_PATH_ENV = "SW_SOURCING_DB_PATH"
_DEFAULT_DB_PATH = "sw_sourcing.db"
_FACEBOOK_INBOX_ENV = "FACEBOOK_INBOX_DIR"
_DEFAULT_FACEBOOK_INBOX = "facebook_inbox"


def build_adapters(config: Config) -> dict[str, Adapter]:
    """Every source this CLI knows how to wire, filtered to what's enabled.

    A source listed in config but not registered here (e.g. a tier-2 source
    awaiting an Apify actor) is silently skipped rather than crashing --
    widening the net is a later, incremental phase per the spec.
    """
    registry: dict[str, Adapter] = {}

    ebay_token = os.environ.get("EBAY_OAUTH_TOKEN")
    if ebay_token:
        registry["ebay"] = EbayAdapter(
            app_token=ebay_token, query="vintage kenner star wars"
        )

    inbox_dir = os.environ.get(_FACEBOOK_INBOX_ENV, _DEFAULT_FACEBOOK_INBOX)
    registry["facebook"] = FacebookAssistAdapter(inbox_dir)

    enabled = set(config.get("sources_enabled"))
    return {name: adapter for name, adapter in registry.items() if name in enabled}


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(prog="sw_sourcing")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser("scan", help="Run one scheduled scan")
    scan_parser.add_argument("--source", help="Only run this one source's adapter")

    args = parser.parse_args(argv)

    db = Database(Path(os.environ.get(_DB_PATH_ENV, _DEFAULT_DB_PATH)))
    config = Config(db)
    adapters = build_adapters(config)

    if args.source is not None:
        if args.source not in adapters:
            parser.error(
                f"unknown or unwired source: {args.source!r} "
                f"(available: {sorted(adapters)})"
            )
        adapters = {args.source: adapters[args.source]}

    pipeline = Pipeline(
        adapters=adapters,
        dedupe=Dedupe(db),
        vision=Vision(ClaudeCliVisionClient(), db),
        config=config,
        db=db,
        alerts=DiscordAlerts(os.environ["DISCORD_WEBHOOK_URL"]),
    )
    summary = pipeline.run()
    logger.info(
        "Run complete: ok=%s failed=%s listings_seen=%s alerts_sent=%s",
        summary.sources_ok,
        summary.sources_failed,
        summary.listings_seen,
        summary.alerts_sent,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
