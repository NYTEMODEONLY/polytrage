"""Diagnostic tool — analyze market efficiency and proximity to arbitrage.

Run directly:
    python -m polytrage.diagnose [--max-markets 200] [--deep-scan 10]

Shows:
    - All binary markets sorted by ask sum (closest to arb first)
    - NegRisk groups with cross-bucket ask sums
    - Order book deep scan of closest candidates
    - Summary statistics on market efficiency
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from rich.console import Console
from rich.table import Table

from polytrage.api import PolymarketClient
from polytrage.models import Market

console = Console()
logger = logging.getLogger(__name__)


async def run_diagnose(
    *,
    max_markets: int = 200,
    deep_scan_count: int = 10,
) -> None:
    """Run full market diagnostic."""
    client = PolymarketClient()

    try:
        console.print("[bold cyan]Polytrage Diagnostics[/bold cyan] — Market Efficiency Analysis\n")

        with console.status("[bold cyan]Fetching markets..."):
            markets = await client.fetch_all_active_markets(max_markets=max_markets)

        console.print(f"Fetched [bold]{len(markets)}[/bold] active markets\n")

        # ── Binary Markets ─────────────────────────────────────
        binary = [m for m in markets if not m.neg_risk and len(m.outcomes) == 2]
        console.print(f"[bold]Binary Markets[/bold] ({len(binary)} total) — Order Book Analysis\n")

        binary_results: list[tuple[float, float, float, str]] = []
        with console.status(f"[bold cyan]Fetching order books for {min(len(binary), deep_scan_count * 3)} binary markets..."):
            for m in binary[:deep_scan_count * 3]:
                try:
                    books = await client.fetch_orderbooks_for_market(m)
                    asks = [ob.best_ask for ob in books if ob.best_ask is not None]
                    bids = [ob.best_bid for ob in books if ob.best_bid is not None]
                    if len(asks) == 2 and len(bids) == 2:
                        ask_sum = sum(asks)
                        bid_sum = sum(bids)
                        net = (1.0 - ask_sum) * 0.98 if ask_sum < 1.0 else (1.0 - ask_sum)
                        binary_results.append((ask_sum, bid_sum, net, m.question[:55]))
                except Exception:
                    pass

        binary_results.sort(key=lambda r: r[0])

        table = Table(title=f"Binary Markets — Closest to Arbitrage")
        table.add_column("Ask Sum", justify="right", width=10)
        table.add_column("Bid Sum", justify="right", width=10)
        table.add_column("Spread", justify="right", width=10)
        table.add_column("Net Profit", justify="right", width=10)
        table.add_column("Market", max_width=55)

        for ask_sum, bid_sum, net, q in binary_results:
            spread = ask_sum - bid_sum
            profit_style = "bold green" if net > 0 else "red" if net < -0.5 else "yellow"
            table.add_row(
                f"${ask_sum:.4f}",
                f"${bid_sum:.4f}",
                f"${spread:.4f}",
                f"[{profit_style}]${net:.4f}[/{profit_style}]",
                f"{'** ARB ** ' if net > 0 else ''}{q}",
            )

        console.print(table)

        # ── NegRisk Markets ────────────────────────────────────
        negrisk = [m for m in markets if m.neg_risk]
        console.print(f"\n[bold]NegRisk Markets[/bold] ({len(negrisk)} total) — Cross-Bucket Group Analysis\n")

        groups: dict[str, list[Market]] = {}
        for m in negrisk:
            parts = m.slug.split("-")
            key = "-".join(parts[:min(4, len(parts))])
            groups.setdefault(key, []).append(m)

        group_results: list[tuple[int, float, float, str]] = []
        multi_groups = {k: v for k, v in groups.items() if len(v) >= 2}

        with console.status(f"[bold cyan]Analyzing {len(multi_groups)} NegRisk groups..."):
            for key, group_markets in list(multi_groups.items())[:deep_scan_count]:
                total_ask = 0.0
                valid = True
                for m in group_markets:
                    try:
                        book = await client.fetch_orderbook(m.clob_token_ids[0])
                        if book.best_ask is None:
                            valid = False
                            break
                        total_ask += book.best_ask
                    except Exception:
                        valid = False
                        break

                if valid:
                    net = (1.0 - total_ask) * 0.98 if total_ask < 1.0 else (1.0 - total_ask)
                    label = group_markets[0].question[:50]
                    group_results.append((len(group_markets), total_ask, net, label))

        group_results.sort(key=lambda r: r[1])

        table2 = Table(title="NegRisk Groups — Cross-Bucket Ask Sums")
        table2.add_column("Buckets", justify="center", width=8)
        table2.add_column("Total Ask", justify="right", width=10)
        table2.add_column("Net Profit", justify="right", width=10)
        table2.add_column("Group", max_width=50)

        for n_out, ask_sum, net, label in group_results:
            profit_style = "bold green" if net > 0 else "red" if net < -0.5 else "yellow"
            table2.add_row(
                str(n_out),
                f"${ask_sum:.4f}",
                f"[{profit_style}]${net:.4f}[/{profit_style}]",
                f"{'** ARB ** ' if net > 0 else ''}{label}",
            )

        console.print(table2)

        # ── Summary ────────────────────────────────────────────
        console.print("\n[bold]Summary[/bold]")
        binary_arbs = sum(1 for r in binary_results if r[2] > 0)
        negrisk_arbs = sum(1 for r in group_results if r[2] > 0)
        console.print(f"  Binary arbitrage opportunities:  [bold]{binary_arbs}[/bold]")
        console.print(f"  NegRisk arbitrage opportunities: [bold]{negrisk_arbs}[/bold]")

        if binary_results:
            closest = binary_results[0]
            console.print(f"  Closest binary to arb:  ask_sum=${closest[0]:.4f} (need < $1.00)")
        if group_results:
            closest_nr = group_results[0]
            console.print(f"  Closest NegRisk to arb: ask_sum=${closest_nr[1]:.4f} (need < $1.00)")

        if binary_arbs == 0 and negrisk_arbs == 0:
            console.print("\n  [dim]Market is efficiently priced — no arbitrage detected.[/dim]")
            console.print("  [dim]Run continuously with `polytrage --paper` to catch fleeting opportunities.[/dim]")

    finally:
        await client.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="polytrage-diagnose",
        description="Analyze Polymarket efficiency and proximity to arbitrage",
    )
    parser.add_argument("--max-markets", type=int, default=200, help="Markets to fetch (default: 200)")
    parser.add_argument("--deep-scan", type=int, default=10, help="Number of markets/groups to deep scan (default: 10)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING)
    asyncio.run(run_diagnose(max_markets=args.max_markets, deep_scan_count=args.deep_scan))


if __name__ == "__main__":
    main()
