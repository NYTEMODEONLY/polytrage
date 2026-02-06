"""Main bot loop + CLI entry point for Polytrage."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from dataclasses import dataclass, field

from rich.console import Console
from rich.table import Table
from rich.text import Text

from polytrage.api import PolymarketClient
from polytrage.config import Config, load_config
from polytrage.health import health_command, write_heartbeat
from polytrage.logging_setup import setup_logging
from polytrage.models import ArbitrageOpportunity
from polytrage.notify import Notifier
from polytrage.profit import evaluate_opportunity
from polytrage.scanner import ScanConfig, ScanResult, Scanner
from polytrage.storage import TradeStore

console = Console()
logger = logging.getLogger("polytrage")

# Circuit breaker / backoff constants
INITIAL_BACKOFF = 30
MAX_BACKOFF = 600  # 10 minutes
MAX_CONSECUTIVE_FAILURES = 10


@dataclass
class PaperTrade:
    market_question: str
    total_cost: float
    net_profit: float
    roi_pct: float
    timestamp: float = field(default_factory=time.time)


@dataclass
class PaperPortfolio:
    trades: list[PaperTrade] = field(default_factory=list)
    total_invested: float = 0.0
    total_profit: float = 0.0

    def record_trade(self, opp: ArbitrageOpportunity) -> PaperTrade:
        trade = PaperTrade(
            market_question=opp.market.question[:80],
            total_cost=opp.total_cost,
            net_profit=opp.net_profit,
            roi_pct=opp.roi_pct,
        )
        self.trades.append(trade)
        self.total_invested += opp.total_cost
        self.total_profit += opp.net_profit
        return trade

    @property
    def total_roi_pct(self) -> float:
        if self.total_invested <= 0:
            return 0.0
        return (self.total_profit / self.total_invested) * 100.0


def build_opportunities_table(
    opportunities: list[ArbitrageOpportunity],
    scan_result: ScanResult,
) -> Table:
    """Build a Rich table showing current arbitrage opportunities."""
    table = Table(
        title=f"Polytrage Scanner — {scan_result.markets_scanned} markets scanned",
        show_lines=True,
    )
    table.add_column("#", style="dim", width=3)
    table.add_column("Market", style="cyan", max_width=50)
    table.add_column("Type", style="blue", width=8)
    table.add_column("Outcomes", width=8, justify="center")
    table.add_column("Total Cost", justify="right", width=10)
    table.add_column("Net Profit", justify="right", width=10)
    table.add_column("ROI %", justify="right", width=8)
    table.add_column("KL Div", justify="right", width=8)

    if not opportunities:
        table.add_row("—", "No arbitrage opportunities found", "", "", "", "", "", "")
        return table

    for i, opp in enumerate(opportunities, 1):
        # Calculate KL divergence for display
        ask_prices = [o.best_ask for o in opp.outcomes if o.best_ask is not None]
        guarantee = evaluate_opportunity(ask_prices)

        # Color-code profit
        profit_style = "green" if opp.net_profit > 0.01 else "yellow"
        roi_style = "bold green" if opp.roi_pct > 1.0 else "green"

        table.add_row(
            str(i),
            opp.market.question[:50],
            opp.market_type.value,
            str(opp.market.num_outcomes),
            f"${opp.total_cost:.4f}",
            Text(f"${opp.net_profit:.4f}", style=profit_style),
            Text(f"{opp.roi_pct:.2f}%", style=roi_style),
            f"{guarantee.kl_divergence:.4f}",
        )

    return table


def build_paper_portfolio_table(portfolio: PaperPortfolio) -> Table:
    """Build a Rich table showing paper trading results."""
    table = Table(title="Paper Portfolio")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")

    table.add_row("Total Trades", str(len(portfolio.trades)))
    table.add_row("Total Invested", f"${portfolio.total_invested:.4f}")
    table.add_row("Total Profit", f"${portfolio.total_profit:.4f}")
    table.add_row("Overall ROI", f"{portfolio.total_roi_pct:.2f}%")

    return table


async def run_scan_loop(
    *,
    interval: int = 60,
    min_profit: float = 0.005,
    max_markets: int = 100,
    fee_rate: float = 0.02,
    paper: bool = False,
    use_orderbooks: bool = True,
    min_liquidity: float = 0.0,
    min_volume: float = 0.0,
    once: bool = False,
    headless: bool = False,
    config: Config | None = None,
    notifier: Notifier | None = None,
    store: TradeStore | None = None,
) -> None:
    """Main scan loop with error recovery and circuit breaker."""
    cfg = config or Config()

    scan_config = ScanConfig(
        max_markets=max_markets,
        min_profit=min_profit,
        fee_rate=fee_rate,
        use_orderbooks=use_orderbooks,
        min_liquidity=min_liquidity,
        min_volume=min_volume,
    )

    client = PolymarketClient(
        concurrency=cfg.api.concurrency,
        timeout=cfg.api.timeout,
        client_refresh_interval=cfg.api.client_refresh_interval,
    )
    scanner = Scanner(client=client, config=scan_config)
    portfolio = PaperPortfolio() if paper else None

    if not headless:
        console.print("[bold]Polytrage[/bold] — Polymarket Arbitrage Scanner", style="cyan")
        console.print(f"Config: interval={interval}s, min_profit=${min_profit}, max_markets={max_markets}")
        if paper:
            console.print("[yellow]Paper trading mode — no real trades[/yellow]")
        console.print()

    # Notify startup
    if notifier:
        await notifier.notify_startup(
            f"interval={interval}s, markets={max_markets}, paper={paper}"
        )

    consecutive_failures = 0
    backoff = INITIAL_BACKOFF

    try:
        while True:
            scan_start = time.time()

            try:
                if not headless:
                    with console.status("[bold cyan]Scanning markets..."):
                        result = await scanner.scan()
                else:
                    result = await scanner.scan()

                # Reset backoff on success
                consecutive_failures = 0
                backoff = INITIAL_BACKOFF

                # Display results
                if not headless:
                    table = build_opportunities_table(result.opportunities, result)
                    console.print(table)

                # Paper trading
                if portfolio and result.opportunities:
                    for opp in result.opportunities:
                        portfolio.record_trade(opp)

                        # Persist trade
                        if store:
                            store.record(
                                market_id=opp.market.id,
                                market_question=opp.market.question,
                                total_cost=opp.total_cost,
                                net_profit=opp.net_profit,
                                roi_pct=opp.roi_pct,
                            )

                        # Discord notification
                        if notifier:
                            await notifier.notify_arb(
                                market_id=opp.market.id,
                                market_question=opp.market.question,
                                net_profit=opp.net_profit,
                                roi_pct=opp.roi_pct,
                                total_cost=opp.total_cost,
                            )

                    if not headless:
                        console.print(build_paper_portfolio_table(portfolio))

                # Log errors from scan (non-fatal)
                for error in result.errors:
                    if not headless:
                        console.print(f"[red]Error:[/red] {error}")
                    logger.warning("Scan error: %s", error)

                # Health heartbeat
                write_heartbeat(
                    cfg.health,
                    markets_scanned=result.markets_scanned,
                    opportunities=len(result.opportunities),
                    errors=len(result.errors),
                )

                scan_duration = time.time() - scan_start
                if not headless:
                    console.print(
                        f"\n[dim]Scan completed in {scan_duration:.1f}s — "
                        f"found {len(result.opportunities)} opportunities[/dim]"
                    )
                logger.info(
                    "Scan completed: %d markets, %d opportunities, %.1fs",
                    result.markets_scanned, len(result.opportunities), scan_duration,
                )

            except Exception:
                consecutive_failures += 1
                logger.exception(
                    "Scan failed (consecutive: %d/%d)",
                    consecutive_failures, MAX_CONSECUTIVE_FAILURES,
                )

                # Circuit breaker
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    msg = f"Circuit breaker: {MAX_CONSECUTIVE_FAILURES} consecutive failures, shutting down"
                    logger.critical(msg)
                    if notifier:
                        await notifier.notify_error(msg)
                    if not headless:
                        console.print(f"[bold red]{msg}[/bold red]")
                    break

                if not headless:
                    console.print(
                        f"[red]Scan failed, retrying in {backoff}s "
                        f"({consecutive_failures}/{MAX_CONSECUTIVE_FAILURES})[/red]"
                    )

                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF)
                continue  # Skip normal sleep, we already waited

            if once:
                break

            if not headless:
                console.print(f"[dim]Next scan in {interval}s...[/dim]\n")
            await asyncio.sleep(interval)

    except KeyboardInterrupt:
        if not headless:
            console.print("\n[yellow]Stopped by user[/yellow]")
        logger.info("Stopped by user")
    finally:
        await client.close()
        if notifier:
            await notifier.close()
        if portfolio and portfolio.trades and not headless:
            console.print("\n[bold]Final Paper Portfolio:[/bold]")
            console.print(build_paper_portfolio_table(portfolio))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="polytrage",
        description="Polymarket arbitrage scanner",
    )

    # Subcommands
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("health", help="Check bot health (exit 0=ok, 1=stale)")

    parser.add_argument(
        "--config", type=str, default=None,
        help="Path to TOML config file (default: polytrage.toml)",
    )
    parser.add_argument(
        "--interval", type=int, default=None,
        help="Scan interval in seconds (default: 60)",
    )
    parser.add_argument(
        "--min-profit", type=float, default=None,
        help="Minimum net profit threshold in dollars (default: 0.005)",
    )
    parser.add_argument(
        "--max-markets", type=int, default=None,
        help="Maximum number of markets to scan (default: 100)",
    )
    parser.add_argument(
        "--fee-rate", type=float, default=None,
        help="Fee rate on winnings (default: 0.02 = 2%%)",
    )
    parser.add_argument(
        "--paper", action="store_true", default=None,
        help="Enable paper trading mode (simulate trades, track P&L)",
    )
    parser.add_argument(
        "--no-orderbooks", action="store_true",
        help="Skip order book fetching, use midpoint prices only (faster but less accurate)",
    )
    parser.add_argument(
        "--min-liquidity", type=float, default=None,
        help="Minimum market liquidity filter",
    )
    parser.add_argument(
        "--min-volume", type=float, default=None,
        help="Minimum market volume filter",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Run a single scan and exit",
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="VPS mode: suppress Rich console output, log WARNING+ to console",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable verbose logging",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    # Determine config path
    config_path = args.config
    if config_path is None:
        from pathlib import Path
        default = Path("polytrage.toml")
        if default.exists():
            config_path = str(default)

    cfg = load_config(config_path)

    # CLI args override config (only if explicitly provided)
    if args.interval is not None:
        cfg.scan.interval = args.interval
    if args.min_profit is not None:
        cfg.scan.min_profit = args.min_profit
    if args.max_markets is not None:
        cfg.scan.max_markets = args.max_markets
    if args.fee_rate is not None:
        cfg.scan.fee_rate = args.fee_rate
    if args.min_liquidity is not None:
        cfg.scan.min_liquidity = args.min_liquidity
    if args.min_volume is not None:
        cfg.scan.min_volume = args.min_volume
    if args.paper is True:
        cfg.paper = True
    if args.no_orderbooks:
        cfg.scan.use_orderbooks = False
    if args.headless:
        cfg.headless = True

    # Health subcommand — quick exit, no logging setup needed
    if args.command == "health":
        health_command(cfg.health)
        return  # health_command calls sys.exit

    # Setup logging
    setup_logging(cfg.log, headless=cfg.headless, verbose=args.verbose)

    # Setup notifier
    notifier = Notifier(cfg.notify)

    # Setup trade store
    store: TradeStore | None = None
    if cfg.paper and cfg.storage.enabled:
        store = TradeStore(cfg.storage)
        store.load()

    asyncio.run(run_scan_loop(
        interval=cfg.scan.interval,
        min_profit=cfg.scan.min_profit,
        max_markets=cfg.scan.max_markets,
        fee_rate=cfg.scan.fee_rate,
        paper=cfg.paper,
        use_orderbooks=cfg.scan.use_orderbooks,
        min_liquidity=cfg.scan.min_liquidity,
        min_volume=cfg.scan.min_volume,
        once=args.once,
        headless=cfg.headless,
        config=cfg,
        notifier=notifier,
        store=store,
    ))


if __name__ == "__main__":
    main()
