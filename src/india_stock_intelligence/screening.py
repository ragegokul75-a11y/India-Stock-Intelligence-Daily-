from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone


def _pct_change(current: float, prior: float) -> float | None:
    if prior == 0:
        return None
    return (current / prior - 1.0) * 100.0


def calculate_metrics(closes: list[float], volumes: list[float | None]) -> dict:
    """Calculate only metrics supported by observed values; insufficient history stays null."""
    result = {
        "return_1d_pct": None, "return_20d_pct": None, "return_60d_pct": None,
        "return_252d_pct": None, "ma_50": None, "ma_200": None,
        "volatility_20d_pct": None, "max_drawdown_252d_pct": None,
        "volume_change_20d_pct": None,
    }
    if not closes:
        return result
    if len(closes) >= 2:
        result["return_1d_pct"] = _pct_change(closes[-1], closes[-2])
    for days, key in ((20, "return_20d_pct"), (60, "return_60d_pct"), (252, "return_252d_pct")):
        if len(closes) >= days + 1:
            result[key] = _pct_change(closes[-1], closes[-days - 1])
    if len(closes) >= 50:
        result["ma_50"] = sum(closes[-50:]) / 50
    if len(closes) >= 200:
        result["ma_200"] = sum(closes[-200:]) / 200
    if len(closes) >= 21:
        returns = [_pct_change(closes[i], closes[i - 1]) for i in range(len(closes) - 20, len(closes))]
        returns = [value for value in returns if value is not None]
        if len(returns) == 20:
            mean = sum(returns) / len(returns)
            variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
            result["volatility_20d_pct"] = math.sqrt(variance) * math.sqrt(252)
    if len(closes) >= 252:
        window = closes[-252:]
        peak = window[0]
        drawdown = 0.0
        for value in window:
            peak = max(peak, value)
            drawdown = min(drawdown, (value / peak - 1.0) * 100.0)
        result["max_drawdown_252d_pct"] = drawdown
    valid_volumes = [value for value in volumes[-20:] if value is not None]
    if len(volumes) >= 20 and len(valid_volumes) == 20:
        prior_avg = sum(valid_volumes[:15]) / 15
        recent_avg = sum(valid_volumes[15:]) / 5
        result["volume_change_20d_pct"] = _pct_change(recent_avg, prior_avg)
    return result


def run_screening(conn: sqlite3.Connection, data_date: str | None = None) -> int:
    if data_date is None:
        row = conn.execute("SELECT MAX(price_date) AS latest FROM daily_prices WHERE close IS NOT NULL").fetchone()
        data_date = row["latest"]
    if not data_date:
        return 0
    created_at = datetime.now(timezone.utc).isoformat()
    securities = conn.execute("SELECT security_key FROM securities ORDER BY exchange, symbol").fetchall()
    count = 0
    for security in securities:
        observations = conn.execute(
            "SELECT close, volume FROM daily_prices WHERE security_key = ? AND price_date <= ? AND close IS NOT NULL ORDER BY price_date",
            (security["security_key"], data_date),
        ).fetchall()
        closes = [float(row["close"]) for row in observations]
        volumes = [None if row["volume"] is None else float(row["volume"]) for row in observations]
        metrics = calculate_metrics(closes, volumes)
        conn.execute(
            """INSERT INTO screening_results
               (security_key, data_date, return_1d_pct, return_20d_pct, return_60d_pct, return_252d_pct,
                ma_50, ma_200, volatility_20d_pct, max_drawdown_252d_pct, volume_change_20d_pct,
                created_at, metrics_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(security_key, data_date) DO UPDATE SET
                 return_1d_pct=excluded.return_1d_pct,
                 return_20d_pct=excluded.return_20d_pct,
                 return_60d_pct=excluded.return_60d_pct,
                 return_252d_pct=excluded.return_252d_pct,
                 ma_50=excluded.ma_50,
                 ma_200=excluded.ma_200,
                 volatility_20d_pct=excluded.volatility_20d_pct,
                 max_drawdown_252d_pct=excluded.max_drawdown_252d_pct,
                 volume_change_20d_pct=excluded.volume_change_20d_pct,
                 created_at=excluded.created_at,
                 metrics_json=excluded.metrics_json""",
            (security["security_key"], data_date, metrics["return_1d_pct"], metrics["return_20d_pct"],
             metrics["return_60d_pct"], metrics["return_252d_pct"], metrics["ma_50"], metrics["ma_200"],
             metrics["volatility_20d_pct"], metrics["max_drawdown_252d_pct"], metrics["volume_change_20d_pct"],
             created_at, json.dumps(metrics, sort_keys=True)),
        )
        count += 1
    conn.commit()
    return count
