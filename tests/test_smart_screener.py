from india_stock_intelligence.smart_screener import infer_sector, parse_query, screen_stocks

STOCKS = [
    {"s": "IDEA", "n": "VODAFONE IDEA LIMITED", "c": 8.5, "v": 9e8, "r1": 2.0, "r5": 5.0, "up5": 4, "nd": 5, "vr": 2.1},
    {"s": "SBIN", "n": "STATE BANK OF INDIA", "c": 820.0, "v": 1e7, "r1": -1.0, "r5": -2.0, "up5": 1, "nd": 5, "vr": 1.0},
    {"s": "PNB", "n": "PUNJAB NATIONAL BANK", "c": 95.0, "v": 5e7, "r1": 1.5, "r5": 3.0, "up5": 3, "nd": 5, "vr": 1.2},
    {"s": "SUNPHARMA", "n": "SUN PHARMACEUTICAL IND L", "c": 1700.0, "v": 2e6, "r1": 0.8, "r5": 1.0, "up5": 3, "nd": 5, "vr": 0.9},
    {"s": "GOLDBEES", "n": "NIP IND ETF GOLD BEES", "c": 9.0, "v": 1e8, "r1": 0.1, "r5": 0.2, "up5": 3, "nd": 5, "vr": 1.0},
]


def test_parse_price_and_sector():
    f = parse_query("banking stocks under ₹500")
    assert f["max_price"] == 500 and f["sectors"] == ["Banking"]
    f = parse_query("IT stocks between 1,000 and 5000")
    assert (f["min_price"], f["max_price"]) == (1000, 5000) and "IT / Software" in f["sectors"]


def test_penny_excludes_etfs_and_expensive():
    rows, f = screen_stocks(STOCKS, "penny stocks")
    assert [r["Symbol"] for r in rows] == ["IDEA"]


def test_sector_and_direction():
    assert infer_sector("PUNJAB NATIONAL BANK", "PNB") == "Banking"
    rows, _ = screen_stocks(STOCKS, "bank gainers")
    assert [r["Symbol"] for r in rows] == ["PNB"]
    rows, _ = screen_stocks(STOCKS, "pharma")
    assert rows[0]["Symbol"] == "SUNPHARMA"
