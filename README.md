# hb-market-simulator

A composable market simulation framework for Hummingbot strategy testing, enabling deterministic backtesting and sandbox deployment of trading strategies.

## Overview

`hb-market-simulator` provides a lightweight, extensible simulation engine for testing trading strategies in realistic market conditions. It is architected as an independent sub-package of the Hummingbot ecosystem, enabling standalone usage or seamless integration with Hummingbot via the compatibility layer.

The simulator supports multiple asset types, fee models, and order matching engines, allowing strategy developers to validate logic before deployment on live exchanges.

## Installation

```bash
pip install hb-market-simulator
```

Or with pixi:

```bash
pixi add hb-market-simulator
```

## Quick Start

```python
import asyncio
from market_simulator import SandboxEngine
from market_simulator.order_book import OrderBook

async def main():
    # Create an order book
    order_book = OrderBook(trading_pair="BTC-USD")
    
    # Create a sandbox engine
    engine = SandboxEngine(order_books=[order_book])
    
    # Start the simulation
    await engine.start()
    
    # Submit orders and run strategy logic
    # ...
    
    # Stop the simulation
    await engine.stop()

asyncio.run(main())
```

## Hummingbot Integration

When used with Hummingbot, the `hb_compat` layer provides drop-in compatibility with strategy and connector interfaces:

```python
from market_simulator.hb_compat import SandboxConnector

# Use with StrategyV2Base or other strategy frameworks
```

## Development Setup

### Prerequisites

- Python 3.12 or higher
- [pixi](https://pixi.sh) (recommended) or hatch/uv

### Setup with pixi

```bash
git clone https://github.com/MementoRC/hb-market-simulator.git
cd hb-market-simulator

pixi install

pixi run test
pixi run lint
pixi run quality
```

### Available Tasks

| Task | Description |
|------|-------------|
| `pixi run test` | Run all tests |
| `pixi run test-unit` | Run unit tests only |
| `pixi run test-integration` | Run integration tests |
| `pixi run lint` | Run ruff linter |
| `pixi run format` | Format code with ruff |
| `pixi run format-check` | Check formatting without changes |
| `pixi run typecheck` | Run mypy type checking |
| `pixi run quality` | Run lint and type checks |
| `pixi run check` | Run all quality checks and tests |

## Architecture

The market simulator is organized into core components:

- **OrderBook**: Maintains buy/sell side order state
- **MatchingEngine**: Implements order matching logic
- **SandboxEngine**: Coordinates simulation lifecycle
- **BalanceManager**: Tracks account balances and collateral
- **FeesModel**: Configurable trading fee calculations
- **Metrics**: Collects simulation statistics (PnL, Sharpe ratio, drawdown)
- **Events**: Pub/sub event system for market and order updates
- **Types**: Enums and data classes for orders, trades, and market state

## Contributing

See [CONTRIBUTING.md](.github/CONTRIBUTING.md) for guidelines on contributing to this project.

## License

Apache-2.0 — see [LICENSE](LICENSE)

## Related Projects

- [hb-rate-oracle](https://github.com/MementoRC/hb-rate-oracle) - Cryptocurrency rate oracle
- [Hummingbot](https://github.com/hummingbot/hummingbot) - The parent project
