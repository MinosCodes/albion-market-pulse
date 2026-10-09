from __future__ import annotations

import argparse
from datetime import datetime, timezone
import logging
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.text import Text

from albion_flips.analyzer import analyze_flips
from albion_flips.api import AodpClient, OfflineClient
from albion_flips.config import AppConfig, ConfigError, load_config, load_items
from albion_flips.models import ExitType, FlipOpportunity
from albion_flips.notify import Notifier
from albion_flips.web import ITEM_NAMES, FlipDataStore, start_web_server


logger = logging.getLogger("albion_flips")
console = Console()


err_console = Console(stderr=True)


def build_table(
    flips: list[FlipOpportunity],
    stack_size: int,
    top_n: int = 25,
    last_refresh: str = "",
    server: str = "europe",
) -> Table:
    """Constructs a Rich table displaying flip opportunities."""
    table = Table(
        title=f"Albion Market Flips ({server.title()} Server) — Last Refresh: {last_refresh}",
        title_style="bold gold1",
        border_style="bright_black",
        header_style="bold cyan",
        show_lines=False,
    )

    table.add_column("Item", style="bold white", no_wrap=True)
    table.add_column("Buy City", style="bright_white")
    table.add_column("Sell City", style="bright_white")
    table.add_column("Buy Price", justify="right", style="cyan")
    table.add_column("Sell Price", justify="right", style="cyan")
    table.add_column("Exit Mode", justify="center")
    table.add_column("Profit / Item", justify="right", style="bold green")
    table.add_column("Margin %", justify="right", style="bold yellow")
    table.add_column(f"Total Profit ({stack_size}x)", justify="right", style="bold green")
    table.add_column("Avg Daily Vol", justify="right")
    table.add_column("Data Age", justify="right", style="dim")
    table.add_column("Risk", justify="center")

    display_rows = flips[:top_n]
    for flip in display_rows:
        exit_badge = (
            Text("Order", style="cyan")
            if flip.exit_type == ExitType.SELL_ORDER
            else Text("Instant", style="magenta")
        )

        vol_text = (
            Text(f"{int(round(flip.avg_daily_volume)):,}", style="white")
            if flip.avg_daily_volume is not None
            else Text("n/a ⚠", style="bold yellow")
        )

        risk_lower = flip.risk.lower()
        if risk_lower == "high":
            risk_style = "bold red"
        elif risk_lower == "medium":
            risk_style = "bold yellow"
        else:
            risk_style = "bold green"
        risk_text = Text(flip.risk.upper(), style=risk_style)

        table.add_row(
            flip.item_id,
            flip.buy_city,
            flip.sell_city,
            f"{flip.buy_price:,}",
            f"{flip.sell_price:,}",
            exit_badge,
            f"{int(flip.profit_per_item):,}",
            f"{flip.margin_pct:.1f}%",
            f"{int(flip.total_profit):,}",
            vol_text,
            f"{int(round(flip.data_age_minutes))}m",
            risk_text,
        )

    return table


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Albion Market Analyzer: Real-time city-to-city market flipping."
    )
    parser.add_argument("--once", action="store_true", help="Perform a single market refresh and exit.")
    parser.add_argument("--watch", action="store_true", help="Continuously refresh and update table.")
    parser.add_argument("--web", action="store_true", help="Start local web dashboard at http://127.0.0.1:8765.")
    parser.add_argument("--config", type=str, default=None, help="Path to config.json.")
    parser.add_argument("--items", type=str, default="data/items.json", help="Path to items.json.")
    parser.add_argument("--offline", type=str, default=None, help="Directory containing fixture JSON files.")
    parser.add_argument("--top", type=int, default=None, help="Number of top rows to display.")
    parser.add_argument(
        "--sort",
        type=str,
        choices=["profit", "total", "margin"],
        default="profit",
        help="Sort column for ranking.",
    )
    return parser.parse_args(args)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    # Load configuration
    try:
        config = load_config(args.config)
    except (FileNotFoundError, ConfigError) as e:
        err_console.print(f"[bold red]Configuration error:[/bold red] {e}")
        return 1

    if args.top:
        config.top_n = args.top

    # Setup offline mode overrides if specified
    injected_now: datetime | None = None
    if args.offline:
        client = OfflineClient(args.offline)
        # Fixture timestamps are fixed at 2026-10-09T08:30:00 UTC
        injected_now = datetime(2026, 10, 9, 8, 30, 0, tzinfo=timezone.utc)
        if config.min_profit_silver == 500:
            config.min_profit_silver = 100
        item_ids = [
            "T4_PLANKS",
            "T4_METALBAR",
            "T4_LEATHER",
            "T4_CLOTH",
            "T4_STONEBLOCK",
            "T4_HIDE",
        ]
    else:
        # Load items
        try:
            item_ids = load_items(args.items)
        except Exception as e:
            err_console.print(f"[bold red]Error loading items:[/bold red] {e}")
            return 1
        client = AodpClient(
            server=config.server,
            prices_cache_ttl_seconds=min(60, config.refresh_seconds),
        )


    web_enabled = args.web or config.web_enabled
    web_store: FlipDataStore | None = None
    if web_enabled:
        web_store = FlipDataStore()
        start_web_server(web_store, port=config.web_port)

    notifier = Notifier(
        threshold_silver=config.notify_profit_silver,
        notify_every_refresh=bool(args.watch),
    )


    def run_refresh(bypass_cache: bool = False) -> list[FlipOpportunity]:
        now = injected_now or datetime.now(timezone.utc)
        refresh_str = now.strftime("%Y-%m-%d %H:%M:%S UTC")

        try:
            prices = client.get_prices(
                item_ids=item_ids,
                cities=config.cities,
                qualities=config.qualities,
                bypass_cache=bypass_cache,
            )
            if args.offline:
                history = client.get_history(item_ids=item_ids, cities=config.cities, qualities=config.qualities)
            else:
                active_item_ids = list({
                    p.item_id for p in prices if (p.sell_price_min > 0 or p.buy_price_max > 0)
                })
                history = client.get_history(
                    item_ids=active_item_ids if active_item_ids else item_ids[:100],
                    cities=config.cities,
                    qualities=config.qualities,
                )
        except Exception as exc:
            logger.error("API error during refresh: %s", exc)
            console.print(f"[yellow]Warning: Market API fetch failed: {exc}[/yellow]")
            return []

        flips = analyze_flips(prices=prices, history=history, config=config, now=now)

        # Sort based on --sort
        if args.sort == "margin":
            flips.sort(key=lambda x: x.margin_pct, reverse=True)
        elif args.sort == "total":
            flips.sort(key=lambda x: x.total_profit, reverse=True)
        else:
            flips.sort(key=lambda x: x.profit_per_item, reverse=True)

        if web_store:
            web_store.update(
                server=config.server,
                last_refresh=refresh_str,
                opportunities=flips,
                prices=prices,
                now=now,
                config=config,
            )


        notifier.check_and_notify(flips, item_names=ITEM_NAMES)

        table = build_table(
            flips=flips,
            stack_size=config.stack_size,
            top_n=config.top_n,
            last_refresh=refresh_str,
            server=config.server,
        )
        console.clear() if args.watch else None
        console.print(table)
        return flips

    if web_store:
        web_store.set_refresh_callback(lambda: run_refresh(bypass_cache=True))

    # Initial refresh
    flips = run_refresh()

    if args.once or not args.watch:
        return 0

    # Watch loop
    console.print(f"[dim]Watching market every {config.refresh_seconds}s (Press Ctrl+C to quit)...[/dim]")
    try:
        while True:
            time.sleep(config.refresh_seconds)
            run_refresh(bypass_cache=True)

    except KeyboardInterrupt:
        console.print("\n[cyan]Stopped market analyzer.[/cyan]")
        return 0


if __name__ == "__main__":
    sys.exit(main())
