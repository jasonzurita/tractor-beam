"""Cron entrypoint: `python -m sw_sourcing.cli scan [--source NAME]`."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
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
from sw_sourcing.adapters.craigslist import CraigslistAdapter
from sw_sourcing.adapters.ebay import (
    EbayAdapter,
    get_ebay_access_token,
    is_still_listed,
)
from sw_sourcing.adapters.facebook_assist import FacebookAssistAdapter
from sw_sourcing.alerts.discord import DiscordAlerts
from sw_sourcing.alerts.email import EmailSender, format_report
from sw_sourcing.core.dedupe import Dedupe
from sw_sourcing.core.vision import ClaudeCliVisionClient, Vision
from sw_sourcing.dashboard import build_dashboard_data, render_dashboard_html
from sw_sourcing.diagnostics import DEFAULT_REPORTS_DIR, write_report
from sw_sourcing.network import wait_for_network
from sw_sourcing.pipeline import Pipeline
from sw_sourcing.storage.config import DEFAULTS as CONFIG_DEFAULTS
from sw_sourcing.storage.config import Config
from sw_sourcing.storage.db import AlertRecord, Database

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
_DEFAULT_DASHBOARD_PATH = "dashboard.html"
_DASHBOARD_RECENT_LIMIT = 20

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
                app_token=token, queries=config.get("ebay_search_queries")
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

    # Craigslist needs no credentials -- it reads public search RSS feeds --
    # so it's always registered, then filtered by sources_enabled below.
    registry["craigslist"] = CraigslistAdapter(
        site=config.get("craigslist_site"),
        queries=config.get("craigslist_search_queries"),
        categories=config.get("craigslist_categories"),
    )

    enabled = set(config.get("sources_enabled"))
    return {name: adapter for name, adapter in registry.items() if name in enabled}


_FACEBOOK_ITEM_ID_RE = re.compile(r"/marketplace/item/(\d+)")


def add_facebook_listing(
    inbox_dir: Path | str,
    *,
    url: str,
    title: str,
    price: float,
    images: list[str],
    description: str = "",
    location: str | None = None,
    listing_id: str | None = None,
) -> Path:
    """Forward one Facebook Marketplace listing into the inbox the assist
    adapter drains -- the ToS-safe human-in-the-loop path (you're already
    viewing the listing; this just hands its public fields to the pipeline,
    no scraping and no messaging the seller).

    The listing id is taken from `--listing-id` or derived from a
    `/marketplace/item/<id>/` URL; without either there's nothing stable to
    dedupe on, so it's an error rather than a guess.
    """
    if listing_id is None:
        match = _FACEBOOK_ITEM_ID_RE.search(url)
        if match is None:
            raise ValueError(
                "could not derive a listing id from the URL; pass --listing-id"
            )
        listing_id = match.group(1)

    payload: dict[str, Any] = {
        "listing_id": listing_id,
        "url": url,
        "title": title,
        "description": description,
        "price": price,
        "images": images,
    }
    if location is not None:
        payload["location"] = location

    inbox = Path(inbox_dir)
    inbox.mkdir(parents=True, exist_ok=True)
    path = inbox / f"{listing_id}.json"
    path.write_text(json.dumps(payload, indent=2))
    return path


def send_report(
    db: Database,
    *,
    to_addr: str,
    smtp_host: str,
    smtp_port: int,
    smtp_username: str,
    smtp_password: str,
    smtp_factory: Callable[[], Any] | None = None,
    is_still_listed: Callable[[AlertRecord], bool] | None = None,
) -> int:
    """Email everything not yet reported; a no-op (returns 0) if nothing's
    new. Cadence is deliberately not this function's concern -- call it as
    often as you like (e.g. from cron); it only ever sends what's new since
    the last successful send.

    `scan` and `send-report` run on independent cadences, so a listing can
    sell in the gap between being alerted and the digest actually landing.
    `is_still_listed` (default: treat everything as still listed) gets one
    last look right before the email is built; anything it flags as gone is
    dropped from the email and still marked reported -- there's nothing more
    to say about a listing that already sold.
    """
    unreported = db.get_unreported_alerts()
    if not unreported:
        return 0

    check = is_still_listed or (lambda alert: True)
    still_listed: list[AlertRecord] = []
    sold: list[AlertRecord] = []
    for alert in unreported:
        (still_listed if check(alert) else sold).append(alert)

    now = datetime.now(UTC).isoformat()
    if sold:
        db.mark_alerts_reported([alert.id for alert in sold], reported_at=now)

    if not still_listed:
        return 0

    subject, html = format_report(still_listed)
    EmailSender(
        host=smtp_host,
        port=smtp_port,
        username=smtp_username,
        password=smtp_password,
        smtp_factory=smtp_factory,
    ).send(to_addr=to_addr, subject=subject, html_body=html)

    db.mark_alerts_reported([alert.id for alert in still_listed], reported_at=now)
    return len(still_listed)


def _build_availability_checker(
    *,
    ebay_app_id: str | None,
    ebay_cert_id: str | None,
    ebay_token_client: httpx.Client | None = None,
    bug_reports_dir: Path | str = DEFAULT_REPORTS_DIR,
) -> Callable[[AlertRecord], bool] | None:
    """A checker that re-verifies only eBay alerts before they're emailed --
    Facebook is human-in-the-loop with no API to query, so there's no more
    accurate signal to add there; those alerts always pass through.

    Returns None (skip the check entirely) without eBay credentials, or if
    the token fetch fails -- same graceful-degradation posture as
    `build_adapters`, since a failed availability check should never block
    the whole digest.
    """
    if not (ebay_app_id and ebay_cert_id):
        return None
    try:
        token = get_ebay_access_token(
            ebay_app_id, ebay_cert_id, client=ebay_token_client
        )
    except Exception as exc:
        logger.exception("Failed to fetch eBay OAuth token; skipping sold-check")
        write_report(
            summary="Failed to fetch eBay OAuth token for the sold-check",
            context={"app_id_set": True, "cert_id_set": True},
            exception=exc,
            reports_dir=bug_reports_dir,
        )
        return None

    def check(alert: AlertRecord) -> bool:
        if alert.source != "ebay":
            return True
        return is_still_listed(alert.listing_id, app_token=token)

    return check


def _network_ready(config: Config, *, command: str) -> bool:
    """Preflight retry (see `network.py`) before a network-dependent
    command does real work -- not a bug, so no bug report; just a clear log
    line and a clean skip, same as the "another scan is already running"
    skip below.
    """
    ready = wait_for_network(
        max_attempts=config.get("network_check_max_attempts"),
        initial_delay_seconds=config.get("network_check_initial_delay_seconds"),
        max_delay_seconds=config.get("network_check_max_delay_seconds"),
    )
    if not ready:
        logger.warning(
            "No network reachable after retrying; skipping this %s run", command
        )
    return ready


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

    fb_parser = subparsers.add_parser(
        "add-facebook",
        help="Forward a Facebook Marketplace listing you're viewing into the "
        "inbox the scan drains (human-in-the-loop; no scraping)",
    )
    fb_parser.add_argument("--url", required=True, help="The Marketplace listing URL")
    fb_parser.add_argument("--title", required=True, help="Listing title")
    fb_parser.add_argument("--price", required=True, type=float, help="Asking price")
    fb_parser.add_argument(
        "--image",
        action="append",
        default=[],
        dest="images",
        help="An image URL (repeat --image for each photo)",
    )
    fb_parser.add_argument("--description", default="", help="Listing description text")
    fb_parser.add_argument("--location", help="Seller location, e.g. 'Merrick, NY'")
    fb_parser.add_argument(
        "--listing-id",
        dest="listing_id",
        help="Override the id (default: derived from a /marketplace/item/<id>/ URL)",
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

    dashboard_parser = subparsers.add_parser(
        "dashboard", help="Regenerate the local HTML observability dashboard"
    )
    dashboard_parser.add_argument(
        "--out",
        default=_DEFAULT_DASHBOARD_PATH,
        help=f"Where to write the HTML file (default: {_DEFAULT_DASHBOARD_PATH})",
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

    if args.command == "add-facebook":
        inbox_dir = os.environ.get(_FACEBOOK_INBOX_ENV, _DEFAULT_FACEBOOK_INBOX)
        try:
            path = add_facebook_listing(
                inbox_dir,
                url=args.url,
                title=args.title,
                price=args.price,
                images=args.images,
                description=args.description,
                location=args.location,
                listing_id=args.listing_id,
            )
        except ValueError as exc:
            parser.error(str(exc))
        print(f"Forwarded to inbox: {path}")
        return 0

    db_path = os.environ.get(_DB_PATH_ENV, _DEFAULT_DB_PATH)
    db = Database(Path(db_path))

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

    if args.command == "dashboard":
        data = build_dashboard_data(
            db,
            bug_reports_dir=bug_reports_dir,
            generated_at=datetime.now(UTC).isoformat(),
            recent_limit=_DASHBOARD_RECENT_LIMIT,
            db_path=db_path,
            log_path=os.environ.get(_LOG_PATH_ENV, _DEFAULT_LOG_PATH),
            lock_path=os.environ.get(_LOCK_PATH_ENV, _DEFAULT_LOCK_PATH),
        )
        out_path = Path(args.out)
        out_path.write_text(render_dashboard_html(data))
        print(f"Wrote {out_path}")
        return 0

    if args.command == "send-report":
        if not _network_ready(Config(db), command="send-report"):
            return 0
        try:
            checker = _build_availability_checker(
                ebay_app_id=os.environ.get("EBAY_APP_ID"),
                ebay_cert_id=os.environ.get("EBAY_CERT_ID"),
                bug_reports_dir=bug_reports_dir,
            )
            count = send_report(
                db,
                to_addr=os.environ["REPORT_TO_EMAIL"],
                smtp_host=os.environ.get("SMTP_HOST", _DEFAULT_SMTP_HOST),
                smtp_port=int(os.environ.get("SMTP_PORT", _DEFAULT_SMTP_PORT)),
                smtp_username=os.environ["SMTP_USERNAME"],
                smtp_password=os.environ["SMTP_PASSWORD"],
                is_still_listed=checker,
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

    config = Config(db)
    if not _network_ready(config, command="scan"):
        return 0

    lock_path = os.environ.get(_LOCK_PATH_ENV, _DEFAULT_LOCK_PATH)
    with lock.acquire(lock_path) as acquired:
        if not acquired:
            logger.info("Another scan is already running; skipping this run.")
            return 0

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
