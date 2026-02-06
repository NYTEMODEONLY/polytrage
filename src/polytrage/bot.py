"""Main bot loop + CLI entry point for Polytrage."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from dataclasses import dataclass, field

from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.text import Text

from polytrage.api import PolymarketClient
from polytrage.models import ArbitrageOpportunity
from polytrage.profit import evaluate_opportunity
from polytrage.scanner import ScanConfig, ScanResult, Scanner

console = Console()
logger = logging.getLogger("polytrage")


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
) -> None:
    """Main scan loop."""
    config = ScanConfig(
        max_markets=max_markets,
        min_profit=min_profit,
        fee_rate=fee_rate,
        use_orderbooks=use_orderbooks,
        min_liquidity=min_liquidity,
        min_volume=min_volume,
    )

    client = PolymarketClient()
    scanner = Scanner(client=client, config=config)
    portfolio = PaperPortfolio() if paper else None

    console.print("[bold]Polytrage[/bold] — Polymarket Arbitrage Scanner", style="cyan")
    console.print(f"Config: interval={interval}s, min_profit=${min_profit}, max_markets={max_markets}")
    if paper:
        console.print("[yellow]Paper trading mode — no real trades[/yellow]")
    console.print()

    try:
        while True:
            scan_start = time.time()

            with console.status("[bold cyan]Scanning markets..."):
                result = await scanner.scan()

            # Display results
            table = build_opportunities_table(result.opportunities, result)
            console.print(table)

            # Paper trading
            if portfolio and result.opportunities:
                for opp in result.opportunities:
                    portfolio.record_trade(opp)
                console.print(build_paper_portfolio_table(portfolio))

            # Log errors
            for error in result.errors:
                console.print(f"[red]Error:[/red] {error}")

            scan_duration = time.time() - scan_start
            console.print(
                f"\n[dim]Scan completed in {scan_duration:.1f}s — "
                f"found {len(result.opportunities)} opportunities[/dim]"
            )

            if once:
                break

            console.print(f"[dim]Next scan in {interval}s...[/dim]\n")
            await asyncio.sleep(interval)

    except KeyboardInterrupt:
        console.print("\n[yellow]Stopped by user[/yellow]")
    finally:
        await client.close()
        if portfolio and portfolio.trades:
            console.print("\n[bold]Final Paper Portfolio:[/bold]")
            console.print(build_paper_portfolio_table(portfolio))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="polytrage",
        description="Polymarket arbitrage scanner",
    )
    parser.add_argument(
        "--interval", type=int, default=60,
        help="Scan interval in seconds (default: 60)",
    )
    parser.add_argument(
        "--min-profit", type=float, default=0.005,
        help="Minimum net profit threshold in dollars (default: 0.005)",
    )
    parser.add_argument(
        "--max-markets", type=int, default=100,
        help="Maximum number of markets to scan (default: 100)",
    )
    parser.add_argument(
        "--fee-rate", type=float, default=0.02,
        help="Fee rate on winnings (default: 0.02 = 2%%)",
    )
    parser.add_argument(
        "--paper", action="store_true",
        help="Enable paper trading mode (simulate trades, track P&L)",
    )
    parser.add_argument(
        "--no-orderbooks", action="store_true",
        help="Skip order book fetching, use midpoint prices only (faster but less accurate)",
    )
    parser.add_argument(
        "--min-liquidity", type=float, default=0.0,
        help="Minimum market liquidity filter",
    )
    parser.add_argument(
        "--min-volume", type=float, default=0.0,
        help="Minimum market volume filter",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Run a single scan and exit",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable verbose logging",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    asyncio.run(run_scan_loop(
        interval=args.interval,
        min_profit=args.min_profit,
        max_markets=args.max_markets,
        fee_rate=args.fee_rate,
        paper=args.paper,
        use_orderbooks=not args.no_orderbooks,
        min_liquidity=args.min_liquidity,
        min_volume=args.min_volume,
        once=args.once,
    ))


if __name__ == "__main__":
    main()
