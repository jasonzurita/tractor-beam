"""Cron entrypoint: `python -m sw_sourcing.cli scan [--source NAME]`."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

from sw_sourcing.adapters.base import Adapter
from sw_sourcing.adapters.ebay import EbayAdapter, get_ebay_access_token
from sw_sourcing.adapters.facebook_assist import FacebookAssistAdapter
from sw_sourcing.alerts.discord import DiscordAlerts
from sw_sourcing.core.dedupe import Dedupe
from sw_sourcing.core.vision import ClaudeCliVisionClient, Vision
from sw_sourcing.diagnostics import DEFAULT_REPORTS_DIR, write_report
from sw_sourcing.pipeline import Pipeline
from sw_sourcing.storage.config import Config
from sw_sourcing.storage.db import Database

logger = logging.getLogger(__name__)

_DB_PATH_ENV = "SW_SOURCING_DB_PATH"
_DEFAULT_DB_PATH = "sw_sourcing.db"
_FACEBOOK_INBOX_ENV = "FACEBOOK_INBOX_DIR"
_DEFAULT_FACEBOOK_INBOX = "facebook_inbox"
_BUG_REPORTS_DIR_ENV = "SW_SOURCING_BUG_REPORTS_DIR"


def build_adapters(
    config: Config,
    *,
    bug_reports_dir: Path | str = DEFAULT_REPORTS_DIR,
    ebay_token_client: httpx.Client | None = None,
) -> dict[str, Adapter]:
    """Every source this CLI knows how to wire, filtered to what's enabled.

    A source listed in config but not registered here (e.g. a tier-2 source
    awaiting an Apify actor) is silently skipped rather than crashing --
    widening the net is a later, incremental phase per the spec. eBay's
    token is fetched fresh every call from the (non-expiring) App
    ID/Cert ID pair -- nothing to manually refresh. If that fetch fails,
    eBay is skipped for this run rather than crashing the whole scan, and a
    bug report is written so it doesn't go unnoticed.
    """
    registry: dict[str, Adapter] = {}

    app_id = os.environ.get("EBAY_APP_ID")
    cert_id = os.environ.get("EBAY_CERT_ID")
    if app_id and cert_id:
        try:
            token = get_ebay_access_token(app_id, cert_id, client=ebay_token_client)
            registry["ebay"] = EbayAdapter(
                app_token=token, query="vintage kenner star wars"
            )
        except Exception as exc:
            logger.exception("Failed to fetch eBay OAuth token; skipping eBay")
            write_report(
                summary="Failed to fetch eBay OAuth token",
                context={"app_id_set": True, "cert_id_set": True},
                exception=exc,
                reports_dir=bug_reports_dir,
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

    report_parser = subparsers.add_parser(
        "report-bug", help="Manually log something odd for later review"
    )
    report_parser.add_argument(
        "note", help="Free-text description of what looked wrong"
    )

    args = parser.parse_args(argv)
    bug_reports_dir = os.environ.get(_BUG_REPORTS_DIR_ENV, str(DEFAULT_REPORTS_DIR))

    if args.command == "report-bug":
        path = write_report(
            summary=args.note,
            context={"reported_via": "cli"},
            reports_dir=bug_reports_dir,
        )
        print(f"Logged: {path}")
        return 0

    db = Database(Path(os.environ.get(_DB_PATH_ENV, _DEFAULT_DB_PATH)))
    config = Config(db)

    try:
        adapters = build_adapters(config, bug_reports_dir=bug_reports_dir)

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
            bug_reports_dir=bug_reports_dir,
        )
        summary = pipeline.run()
    except Exception as exc:
        logger.exception("Unhandled error during scan; see bug_reports/")
        write_report(
            summary="Unhandled error during scan",
            context={"source_filter": args.source},
            exception=exc,
            reports_dir=bug_reports_dir,
        )
        return 1

    logger.info(
        "Run complete: ok=%s failed=%s listings_seen=%s alerts_sent=%s"
        " bug_reports=%s",
        summary.sources_ok,
        summary.sources_failed,
        summary.listings_seen,
        summary.alerts_sent,
        summary.bug_reports_written,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
