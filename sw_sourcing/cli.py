"""Cron entrypoint: `python -m sw_sourcing.cli scan [--source NAME]`."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from sw_sourcing import lock
from sw_sourcing.adapters.base import Adapter
from sw_sourcing.adapters.ebay import EbayAdapter, get_ebay_access_token
from sw_sourcing.adapters.facebook_assist import FacebookAssistAdapter
from sw_sourcing.alerts.discord import DiscordAlerts
from sw_sourcing.alerts.email import EmailSender, format_report
from sw_sourcing.core.dedupe import Dedupe
from sw_sourcing.core.vision import ClaudeCliVisionClient, Vision
from sw_sourcing.diagnostics import DEFAULT_REPORTS_DIR, write_report
from sw_sourcing.pipeline import Pipeline
from sw_sourcing.storage.config import DEFAULTS as CONFIG_DEFAULTS
from sw_sourcing.storage.config import Config
from sw_sourcing.storage.db import Database

logger = logging.getLogger(__name__)

_DB_PATH_ENV = "SW_SOURCING_DB_PATH"
_DEFAULT_DB_PATH = "sw_sourcing.db"
_FACEBOOK_INBOX_ENV = "FACEBOOK_INBOX_DIR"
_DEFAULT_FACEBOOK_INBOX = "facebook_inbox"
_BUG_REPORTS_DIR_ENV = "SW_SOURCING_BUG_REPORTS_DIR"
_DEFAULT_SMTP_HOST = "smtp.gmail.com"
_DEFAULT_SMTP_PORT = 465
_LOCK_PATH_ENV = "SW_SOURCING_LOCK_PATH"
_DEFAULT_LOCK_PATH = "sw_sourcing.scan.lock"
_LOG_PATH_ENV = "SW_SOURCING_LOG_PATH"
_DEFAULT_LOG_PATH = "sw_sourcing.log"
_LOG_MAX_BYTES = 5 * 1024 * 1024
_LOG_BACKUP_COUNT = 5

_active_log_handler: RotatingFileHandler | None = None


def configure_logging() -> None:
    """Log to a size-rotated file the app manages itself, rather than
    relying on shell `>> file` redirection (which grows unbounded) or an
    external tool like logrotate (not available by default on macOS).

    Idempotent and re-callable: replaces its own previously-installed
    handler rather than accumulating a new one on every call (main() may
    be invoked more than once per process in tests).
    """
    global _active_log_handler
    log_path = os.environ.get(_LOG_PATH_ENV, _DEFAULT_LOG_PATH)
    handler = RotatingFileHandler(
        log_path, maxBytes=_LOG_MAX_BYTES, backupCount=_LOG_BACKUP_COUNT
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )

    root = logging.getLogger()
    if _active_log_handler is not None:
        root.removeHandler(_active_log_handler)
        _active_log_handler.close()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    _active_log_handler = handler


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


def send_report(
    db: Database,
    *,
    to_addr: str,
    smtp_host: str,
    smtp_port: int,
    smtp_username: str,
    smtp_password: str,
    smtp_factory: Callable[[], Any] | None = None,
) -> int:
    """Email everything not yet reported; a no-op (returns 0) if nothing's
    new. Cadence is deliberately not this function's concern -- call it as
    often as you like (e.g. from cron); it only ever sends what's new since
    the last successful send.
    """
    unreported = db.get_unreported_alerts()
    if not unreported:
        return 0

    subject, html = format_report(unreported)
    EmailSender(
        host=smtp_host,
        port=smtp_port,
        username=smtp_username,
        password=smtp_password,
        smtp_factory=smtp_factory,
    ).send(to_addr=to_addr, subject=subject, html_body=html)

    db.mark_alerts_reported(
        [alert.id for alert in unreported], reported_at=datetime.now(UTC).isoformat()
    )
    return len(unreported)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    configure_logging()

    parser = argparse.ArgumentParser(prog="sw_sourcing")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser("scan", help="Run one scheduled scan")
    scan_parser.add_argument("--source", help="Only run this one source's adapter")

    subparsers.add_parser(
        "send-report", help="Email the digest of alerts not yet reported"
    )

    report_parser = subparsers.add_parser(
        "report-bug", help="Manually log something odd for later review"
    )
    report_parser.add_argument(
        "note", help="Free-text description of what looked wrong"
    )

    config_parser = subparsers.add_parser(
        "config", help="Get or set a threshold in the config table"
    )
    config_subparsers = config_parser.add_subparsers(
        dest="config_action", required=True
    )
    config_get_parser = config_subparsers.add_parser("get", help="Print a config value")
    config_get_parser.add_argument("key")
    config_set_parser = config_subparsers.add_parser(
        "set", help='Set a config value (JSON-encoded, e.g. 5.0 or \'["a","b"]\')'
    )
    config_set_parser.add_argument("key")
    config_set_parser.add_argument("value")
    config_subparsers.add_parser("list", help="Print every config key and its value")

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

    if args.command == "config":
        config = Config(db)
        if args.config_action == "list":
            for key in sorted(CONFIG_DEFAULTS):
                print(f"{key} = {json.dumps(config.get(key))}")
            return 0
        if args.config_action == "get":
            try:
                value = config.get(args.key)
            except KeyError as exc:
                parser.error(str(exc))
            print(json.dumps(value))
            return 0
        if args.config_action == "set":
            try:
                parsed_value = json.loads(args.value)
            except json.JSONDecodeError as exc:
                parser.error(f"value must be JSON-encoded: {exc}")
            try:
                config.set(args.key, parsed_value)
            except KeyError as exc:
                parser.error(str(exc))
            print(json.dumps(parsed_value))
            return 0

    if args.command == "send-report":
        try:
            count = send_report(
                db,
                to_addr=os.environ["REPORT_TO_EMAIL"],
                smtp_host=os.environ.get("SMTP_HOST", _DEFAULT_SMTP_HOST),
                smtp_port=int(os.environ.get("SMTP_PORT", _DEFAULT_SMTP_PORT)),
                smtp_username=os.environ["SMTP_USERNAME"],
                smtp_password=os.environ["SMTP_PASSWORD"],
            )
        except Exception as exc:
            logger.exception("Unhandled error sending report; see bug_reports/")
            write_report(
                summary="Unhandled error sending report",
                context={},
                exception=exc,
                reports_dir=bug_reports_dir,
            )
            return 1
        logger.info("Report: %s alert(s) sent", count)
        return 0

    lock_path = os.environ.get(_LOCK_PATH_ENV, _DEFAULT_LOCK_PATH)
    with lock.acquire(lock_path) as acquired:
        if not acquired:
            logger.info("Another scan is already running; skipping this run.")
            return 0

        config = Config(db)
        discord_webhook = os.environ.get("DISCORD_WEBHOOK_URL")

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
                alerts=DiscordAlerts(discord_webhook) if discord_webhook else None,
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
