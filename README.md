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
# Run the test suite (78 tests)
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

## Architecture

```
src/polytrage/
├── models.py      # Pydantic data models (Market, OrderBook, ArbitrageOpportunity)
├── api.py         # Async Polymarket API client (Gamma + CLOB)
├── arbitrage.py   # Arbitrage detection engine (binary + NegRisk markets)
├── profit.py      # KL divergence, Frank-Wolfe gap, profit guarantees
├── scanner.py     # Market scanner (orchestrates API + detection pipeline)
└── bot.py         # CLI entry point, Rich terminal output, paper trading
```

### API Endpoints Used

| Endpoint | Purpose |
|----------|---------|
| `GET gamma-api.polymarket.com/markets` | Fetch active markets with prices |
| `GET clob.polymarket.com/book?token_id=X` | Full order book for an outcome |
| `GET clob.polymarket.com/price?token_id=X&side=buy` | Best ask price |
| `GET clob.polymarket.com/midpoint?token_id=X` | Midpoint price |

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Run with verbose logging
polytrage --once -v
```

### Running Tests

```bash
# All tests
pytest tests/ -v

# Specific module
pytest tests/test_arbitrage.py -v
pytest tests/test_profit.py -v
pytest tests/test_scanner.py -v
pytest tests/test_api.py -v
pytest tests/test_bot.py -v
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
