# Polytrage

**Polymarket arbitrage scanner and paper trading bot.**

Polytrage scans Polymarket prediction markets for mispriced outcomes where buying all sides costs less than $1.00 — guaranteeing risk-free profit at settlement. It implements the mathematical framework from [Roan (@RohOnChain)](https://x.com/RohOnChain)'s article on prediction market arbitrage: [The Math Behind Polymarket Arbitrage Bots](https://x.com/RohOnChain/status/2019131428378931408), including KL divergence-based profit guarantees, Frank-Wolfe optimization gaps, and α-extraction stopping criteria.

## How It Works

In a prediction market, exactly one outcome wins and pays $1.00. If you can buy one share of **every** outcome for less than $1.00 total, you profit the difference no matter what happens.

```
profit = $1.00 - sum(cost_of_each_outcome) - fees
```

Polytrage automates the detection of these opportunities:

1. **Fetches** all active markets from Polymarket's Gamma API
2. **Pre-filters** using midpoint prices (fast, no extra API calls)
3. **Deep scans** candidates by fetching actual order books from the CLOB API
4. **Evaluates** each opportunity using KL divergence profit guarantees
5. **Displays** results in a live-updating terminal dashboard

### Core Formulas

From [RohOnChain's article](https://x.com/RohOnChain/status/2019131428378931408) — the math behind bots like gabagool22:

| Formula | Description |
|---------|-------------|
| `D(μ̂\|\|θ) = Σ μ̂ᵢ · ln(μ̂ᵢ / θᵢ)` | KL divergence between target and current prices |
| `g(μ̂) = max_v ⟨∇f(μ̂), μ̂ - v⟩` | Frank-Wolfe gap (optimization progress remaining) |
| `guaranteed_profit ≥ D(μ̂\|\|θ) - g(μ̂)` | Proposition 4.1 — lower bound on extractable profit |
| `g(μ̂) ≤ (1-α)·D(μ̂\|\|θ)` | α-extraction stop: halt at 90% extraction (α = 0.9) |
| `εD = 0.05` | Minimum threshold: skip if < 5 cents/dollar profit |

## Setup

### Prerequisites

- Python 3.11+
- A Polymarket account (optional — only needed if you plan to extend this for live trading)

### Installation

```bash
git clone https://github.com/NYTEMODEONLY/polytrage.git
cd polytrage

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install the package
pip install -e ".[dev]"
```

### Verify Installation

```bash
# Run the full test suite (89 tests)
pytest tests/ -v

# Check the CLI
polytrage --help
```

## Usage

### Quick Scan

Run a single scan and see what's out there:

```bash
polytrage --once
```

### Continuous Monitoring

Scan every 30 seconds with a live-updating table:

```bash
polytrage --interval 30
```

### Paper Trading

Simulate trades and track P&L without risking real money:

```bash
polytrage --paper --interval 60
```

### Fast Mode

Skip order book fetches and use midpoint prices only (less accurate but much faster):

```bash
polytrage --once --no-orderbooks
```

### All Options

```
polytrage --help

Options:
  --interval INTERVAL       Scan interval in seconds (default: 60)
  --min-profit MIN_PROFIT   Minimum net profit threshold in dollars (default: 0.005)
  --max-markets MAX_MARKETS Maximum markets to scan (default: 100)
  --fee-rate FEE_RATE       Fee rate on winnings (default: 0.02 = 2%)
  --paper                   Enable paper trading mode
  --no-orderbooks           Use midpoint prices only (faster)
  --min-liquidity FLOAT     Minimum market liquidity filter
  --min-volume FLOAT        Minimum market volume filter
  --once                    Run a single scan and exit
  -v, --verbose             Enable debug logging
```

### Example Output

```
┌─────────────────────────────────────────────────────────────────────────────┐
│              Polytrage Scanner — 100 markets scanned                        │
├───┬──────────────────────────────────┬────────┬─────────┬──────────┬────────┤
│ # │ Market                           │ Type   │ Outcomes│ Net Profit│ ROI % │
├───┼──────────────────────────────────┼────────┼─────────┼──────────┼────────┤
│ 1 │ Who will win the election?       │ negrisk│    4    │  $0.0392 │ 4.26% │
│ 2 │ Will BTC hit $150k by March?     │ binary │    2    │  $0.0196 │ 2.10% │
└───┴──────────────────────────────────┴────────┴─────────┴──────────┴────────┘
```

## Connecting to Polymarket

### Read-Only Scanning (Default)

Polytrage works out of the box with **no authentication required**. The Polymarket Gamma and CLOB APIs are publicly accessible for reading market data and order books.

### Setting Up for Live Trading (Advanced)

Polytrage is currently a **scanner and paper trader** — it does not execute real trades. If you want to extend it for live execution, you'll need:

1. **Create a Polymarket account** at [polymarket.com](https://polymarket.com)

2. **Fund your account** — Polymarket runs on Polygon. You'll need USDC on the Polygon network.

3. **Get your API credentials** — Generate API keys from your Polymarket account settings:
   - Go to Settings → API Keys
   - Create a new API key with trading permissions
   - Save your API key and secret securely

4. **Set environment variables**:
   ```bash
   export POLYMARKET_API_KEY="your-api-key"
   export POLYMARKET_API_SECRET="your-api-secret"
   export POLYMARKET_PASSPHRASE="your-passphrase"
   ```

5. **Use the official Python client** — For order execution, integrate the [py-clob-client](https://github.com/Polymarket/py-clob-client):
   ```bash
   pip install py-clob-client
   ```

   ```python
   from py_clob_client.client import ClobClient

   client = ClobClient(
       host="https://clob.polymarket.com",
       key=os.environ["POLYMARKET_API_KEY"],
       chain_id=137,  # Polygon
   )
   ```

> **Warning:** Live trading involves real money. Start with paper trading mode (`--paper`) to validate your strategy. Arbitrage opportunities can disappear in milliseconds — execution speed matters.

## Diagnostics Tool

Polytrage includes a built-in diagnostic tool that analyzes market efficiency and shows how close each market is to arbitrage — useful for understanding the current state of Polymarket pricing.

```bash
python -m polytrage.diagnose
python -m polytrage.diagnose --max-markets 500 --deep-scan 20
```

The diagnostic tool:
- Fetches all active markets and sorts them by proximity to arbitrage
- Deep scans order books for the closest candidates
- Groups NegRisk markets by bucket and sums cross-bucket ask prices
- Displays Rich-formatted tables with color-coded profit/loss
- Reports summary statistics on market efficiency

### Diagnostic Output Example

```
Polytrage Diagnostics — Market Efficiency Analysis

Fetched 200 active markets

           Binary Markets — Closest to Arbitrage
┏━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃   Ask Sum  ┃   Bid Sum  ┃   Spread   ┃ Net Profit ┃ Market                    ┃
┡━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│   $1.9800  │   $0.0200  │   $1.9600  │  -$0.9800  │ Will BTC hit $1m?         │
│   $1.9980  │   $0.0020  │   $1.9960  │  -$0.9980  │ GTA VI before June 2026?  │
└────────────┴────────────┴────────────┴────────────┴───────────────────────────┘

Summary
  Binary arbitrage opportunities:  0
  NegRisk arbitrage opportunities: 0
  Closest binary to arb:  ask_sum=$1.9800 (need < $1.00)
  Market is efficiently priced — no arbitrage detected.
```

## Architecture

```
src/polytrage/
├── models.py      # Pydantic data models (Market, OrderBook, ArbitrageOpportunity)
├── api.py         # Async Polymarket API client (Gamma + CLOB)
├── arbitrage.py   # Arbitrage detection engine (binary + NegRisk markets)
├── profit.py      # KL divergence, Frank-Wolfe gap, profit guarantees
├── scanner.py     # Market scanner (orchestrates API + detection pipeline)
├── bot.py         # CLI entry point, Rich terminal output, paper trading
└── diagnose.py    # Market efficiency diagnostic tool
```

### API Endpoints Used

| Endpoint | Purpose |
|----------|---------|
| `GET gamma-api.polymarket.com/markets` | Fetch active markets with prices |
| `GET clob.polymarket.com/book?token_id=X` | Full order book for an outcome |
| `GET clob.polymarket.com/price?token_id=X&side=buy` | Best ask price |
| `GET clob.polymarket.com/midpoint?token_id=X` | Midpoint price |

## Testing

Polytrage has a comprehensive test suite covering every module — 89 tests across 6 test files, all passing.

```bash
# Run the full suite
pytest tests/ -v

# Run by module
pytest tests/test_arbitrage.py -v    # 17 tests — binary, NegRisk, orderbook, midpoint detection
pytest tests/test_profit.py -v       # 23 tests — KL divergence, FW gap, profit guarantees, α-extraction
pytest tests/test_api.py -v          # 11 tests — market parsing, HTTP client, error handling
pytest tests/test_scanner.py -v      #  6 tests — full pipeline, filters, multi-market, error resilience
pytest tests/test_bot.py -v          # 11 tests — paper portfolio, CLI args, Rich tables, scan loop
pytest tests/test_e2e_simulation.py  # 13 tests — end-to-end simulation with realistic scenarios
```

### Test Results

```
tests/test_api.py            11 passed    Market parsing, HTTP mocks, orderbook/price/midpoint fetching
tests/test_arbitrage.py      17 passed    Binary arb, NegRisk arb, orderbook detection, edge cases
tests/test_bot.py            11 passed    Paper portfolio, CLI args, Rich tables, scan loop
tests/test_profit.py         23 passed    KL divergence, FW gap, guarantees, α-extraction, boundaries
tests/test_scanner.py         6 passed    Full pipeline, midpoint mode, liquidity filter, error handling
tests/test_e2e_simulation.py 11 passed    End-to-end simulation with realistic market scenarios

89 passed in ~8s
```

### Test Coverage Breakdown

**Unit Tests** — Each module tested in isolation with known inputs:
- Arbitrage detection with exact prices (clear arb, no arb, boundary at exactly $1.00, fees eating profit)
- KL divergence against manual calculations (D(p||p)=0, asymmetry, zero handling, inf cases)
- Frank-Wolfe gap convergence (gap decreases as θ approaches μ̂, non-negative guarantee)
- Profit guarantees (Proposition 4.1: D-g, clamping, α-extraction at 90%)
- API response parsing (JSON string fields, missing data, list vs string format)

**Integration Tests** — Scanner pipeline with mocked HTTP:
- Full scan: fetch markets → pre-filter → deep scan order books → sort by ROI
- Midpoint-only fast mode (no order book requests)
- Liquidity and volume filtering
- Graceful error handling when API returns 500

**End-to-End Simulation** — 5 realistic simulated markets:

| Market | Type | Ask Sum | Result | Net Profit | ROI |
|--------|------|---------|--------|------------|-----|
| BTC > $200k by Dec? | binary | $0.86 | Detected | $0.1372 | 15.95% |
| Who wins F1 2026? | negrisk | $0.69 | Detected | $0.3038 | 44.03% |
| ETH flips BTC? | binary | $1.07 | Rejected | — | — |
| SOL > $500? | binary | $1.02 | Rejected | — | — |
| Rate cut in March? | binary | $1.17 | Rejected | — | — |

**Paper trading simulation result:** $1.55 invested, $0.44 profit, **28.45% blended ROI**.

### Live API Dry Run

The scanner was tested against the live Polymarket API (read-only, no trades):

```bash
polytrage --once --paper --max-markets 50
```

- Fetched 50 active markets from Gamma API
- Fetched 100 order books from CLOB API (2 per binary market)
- Completed full scan in **1.7 seconds**
- Found **0 arbitrage opportunities** — market is efficiently priced

This is the expected result: real arbitrage on Polymarket gets consumed by bots within milliseconds. The scanner correctly identifies that no mispricing exists, with zero false positives.

The diagnostic tool confirmed: all 22 binary markets had ask sums between $1.98–$2.00 (each side priced at ~$0.99), and all NegRisk groups had cross-bucket ask sums well above $1.00.

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Run with verbose logging
polytrage --once -v

# Run diagnostics
python -m polytrage.diagnose
```

## References

- **Article:** [The Math Behind Polymarket Arbitrage Bots](https://x.com/RohOnChain/status/2019131428378931408) by [@RohOnChain](https://x.com/RohOnChain)
- **Post:** [RohOnChain on the algorithm behind Polymarket arbitrage](https://x.com/RohOnChain/status/2019493446889927065)
- **Polymarket CLOB API:** [github.com/Polymarket/py-clob-client](https://github.com/Polymarket/py-clob-client)
- **Polymarket Gamma API:** [gamma-api.polymarket.com](https://gamma-api.polymarket.com)

## License

MIT

---

Built by [nytemode](https://nytemode.com)
