"""Historical data loading from CSV and Parquet files.

Loads order book snapshots, trades, and candles into replay-ready format.
Supports both CSV (simple, human-readable) and Parquet (efficient, columnar).
"""

from __future__ import annotations

import csv
import logging
from decimal import Decimal
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


def load_trades_csv(path: Path, trading_pair: str = "") -> List[dict]:
    """Load trades from a CSV file.

    Expected columns: timestamp, trading_pair, trade_id, price, amount, is_buyer_maker
    If trading_pair is empty, uses the value from each row.

    Returns list of dicts with parsed Decimal values, sorted by timestamp.
    """
    trades = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            pair = trading_pair or row.get("trading_pair", "")
            trades.append({
                "timestamp": float(row["timestamp"]),
                "trading_pair": pair,
                "trade_id": row.get("trade_id", ""),
                "price": Decimal(row["price"]),
                "amount": Decimal(row["amount"]),
                "is_buyer_maker": row.get("is_buyer_maker", "false").lower() == "true",
            })
    trades.sort(key=lambda t: t["timestamp"])
    return trades


def load_candles_csv(path: Path, trading_pair: str = "") -> List[dict]:
    """Load OHLCV candles from a CSV file.

    Expected columns: timestamp, open, high, low, close, volume
    Optional: trading_pair, interval

    Returns list of dicts with parsed Decimal values, sorted by timestamp.
    """
    candles = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            pair = trading_pair or row.get("trading_pair", "")
            candles.append({
                "timestamp": float(row["timestamp"]),
                "trading_pair": pair,
                "open": Decimal(row["open"]),
                "high": Decimal(row["high"]),
                "low": Decimal(row["low"]),
                "close": Decimal(row["close"]),
                "volume": Decimal(row["volume"]),
                "interval": row.get("interval", "1m"),
            })
    candles.sort(key=lambda c: c["timestamp"])
    return candles


def load_order_book_snapshots_csv(path: Path, trading_pair: str = "") -> List[dict]:
    """Load order book snapshots from CSV.

    Expected columns: timestamp, side, price, quantity
    Groups by timestamp to form snapshots.

    Returns list of dicts: {timestamp, trading_pair, bids: [...], asks: [...]}
    """
    rows_by_ts: dict[float, dict] = {}

    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = float(row["timestamp"])
            pair = trading_pair or row.get("trading_pair", "")
            if ts not in rows_by_ts:
                rows_by_ts[ts] = {"timestamp": ts, "trading_pair": pair, "bids": [], "asks": []}
            entry = (Decimal(row["price"]), Decimal(row["quantity"]))
            if row["side"].lower() in ("bid", "buy"):
                rows_by_ts[ts]["bids"].append(entry)
            else:
                rows_by_ts[ts]["asks"].append(entry)

    return sorted(rows_by_ts.values(), key=lambda s: s["timestamp"])


def load_trades_parquet(path: Path, trading_pair: str = "") -> List[dict]:
    """Load trades from a Parquet file.

    Expected columns: timestamp, price, amount, is_buyer_maker
    Optional: trading_pair, trade_id
    """
    try:
        import pandas as pd
    except ImportError:
        raise ImportError("pandas is required for Parquet support")

    df = pd.read_parquet(path)
    trades = []
    for _, row in df.iterrows():
        pair = trading_pair or str(row.get("trading_pair", ""))
        trades.append({
            "timestamp": float(row["timestamp"]),
            "trading_pair": pair,
            "trade_id": str(row.get("trade_id", "")),
            "price": Decimal(str(row["price"])),
            "amount": Decimal(str(row["amount"])),
            "is_buyer_maker": bool(row.get("is_buyer_maker", False)),
        })
    trades.sort(key=lambda t: t["timestamp"])
    return trades


def load_candles_parquet(path: Path, trading_pair: str = "") -> List[dict]:
    """Load OHLCV candles from a Parquet file."""
    try:
        import pandas as pd
    except ImportError:
        raise ImportError("pandas is required for Parquet support")

    df = pd.read_parquet(path)
    candles = []
    for _, row in df.iterrows():
        pair = trading_pair or str(row.get("trading_pair", ""))
        candles.append({
            "timestamp": float(row["timestamp"]),
            "trading_pair": pair,
            "open": Decimal(str(row["open"])),
            "high": Decimal(str(row["high"])),
            "low": Decimal(str(row["low"])),
            "close": Decimal(str(row["close"])),
            "volume": Decimal(str(row["volume"])),
            "interval": str(row.get("interval", "1m")),
        })
    candles.sort(key=lambda c: c["timestamp"])
    return candles


def auto_load(path: Path, data_type: str, trading_pair: str = "") -> List[dict]:
    """Auto-detect file format and load data.

    Args:
        path: Path to CSV or Parquet file.
        data_type: One of "trades", "candles", "order_book_snapshots".
        trading_pair: Override trading pair for all records.
    """
    suffix = path.suffix.lower()
    loaders = {
        ("trades", ".csv"): load_trades_csv,
        ("trades", ".parquet"): load_trades_parquet,
        ("candles", ".csv"): load_candles_csv,
        ("candles", ".parquet"): load_candles_parquet,
        ("order_book_snapshots", ".csv"): load_order_book_snapshots_csv,
    }
    loader = loaders.get((data_type, suffix))
    if loader is None:
        raise ValueError(f"No loader for data_type={data_type!r}, format={suffix!r}")
    return loader(path, trading_pair)
