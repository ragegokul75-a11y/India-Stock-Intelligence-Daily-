"""Plain-English NSE stock category search.

Type things like "penny stocks", "banking stocks under 500", "pharma gainers",
"IT stocks between 1000 and 3000", "high volume penny stocks" or "bullish auto".
Everything runs in memory over the 3,551-stock NSE seed (no SQLite, no network).

Sector note: NSE Bhavcopy does not publish a sector field, so the sector is
inferred from the company name / symbol using keyword rules. It is a best-effort
classification and is labelled "Other / Unclassified" when no rule matches.
"""
from __future__ import annotations

import re
from typing import Any

PENNY_MAX_PRICE = 20.0  # ₹ — common Indian retail definition of a penny stock

# Ordered: first match wins. (sector, name/symbol keywords, query aliases)
SECTOR_RULES: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [
    ("ETF / Mutual Fund", ("ETF", "BEES", "AMC-", "AMC -", "NIFTY", "SENSEX", "GILT", "LIQUID", "INAV", " FOF", "AML-", "AML -", "MAFANG", "GOLDCASE", "SILVERCASE", "GOLD ETF", "SILVER ETF"),
     ("etf", "etfs", "index fund", "mutual fund", "bees")),
    ("Banking", ("BANK", "BNK", "BANCORP"), ("bank", "banks", "banking", "psu bank")),
    ("Finance / NBFC", ("FINANCE", "FINANCIAL", "FINSERV", "FINCORP", "CAPITAL", "CREDIT", "LEASING", "INVEST",
                        "HOLDING", "STOCKHOLD", "SECURITIES", "BROKING", "FINTECH", "INSURANCE", "LIFE INS",
                        "HOUSING FIN", "MONEY", "LOANS"),
     ("finance", "financial", "nbfc", "insurance", "fintech", "broking", "capital market")),
    ("IT / Software", ("INFOSYS", "INFOTECH", "INFO TECH", "TECHNOLOG", "SOFTWARE", "COMPUTER",
                       "DIGITAL", "DATA", "CYBER", "INFOSERV", "INFOEDGE", "SOLUTIONS", "TECH ", "WIPRO",
                       "MINDTREE", "CONSULTANCY", "NETWORK", "CLOUD", " AI "),
     ("it stocks", "it sector", "it companies", "it shares", "it company", "software", "tech", "technology", "digital")),
    ("Telecom / Media", ("TELECOM", "COMMUNICAT", "MEDIA", "BROADCAST", "ENTERTAIN", "FILM", "TELEVISION", " TV",
                         "NEWS", "PUBLISH", "AIRTEL", "CABLE"),
     ("telecom", "media", "entertainment", "film", "communication")),
    ("Pharma / Healthcare", ("PHARMA", "DRUG", "LAB", "MEDI", "HEALTH", "HOSPITAL", "REMEDIES", "BIO", "LIFESC",
                             "CHEMIST", "DIAGNOS", "SURGIC", "AYUR", "THERAP", "CLINIC", "VACCIN", "CURE"),
     ("pharma", "pharmaceutical", "healthcare", "health", "hospital", "medical", "medicine")),
    ("Auto / EV", ("AUTO", "MOTOR", "VEHICLE", "TYRE", "TIRE", "MOBILITY", "TRACTOR", "WHEEL", "FORGE", "BEARING",
                   "ELECTRIC MOB", " EV "),
     ("auto", "automobile", "automotive", "car", "ev", "electric vehicle", "tyre", "auto ancillary")),
    ("Power / Energy", ("POWER", "ENERGY", "ENERGIES", "SOLAR", "GREEN", "RENEWABLE", "WIND", "ELECTRIC ", "ELECTRICITY", "GAS ",
                        "OIL", "PETRO", "REFIN", "COAL", "HYDRO", "NTPC", "TRANSMISSION", "UTILIT"),
     ("power", "energy", "solar", "renewable", "oil", "gas", "electricity", "green energy", "coal")),
    ("Metals / Mining", ("STEEL", "METAL", "IRON", "ALUMIN", "COPPER", "ZINC", "MINING", "MINERAL", "ALLOY", "ISPAT",
                         "TUBE", "PIPES", "WIRE", "CASTING"),
     ("metal", "metals", "steel", "mining", "iron", "aluminium", "copper")),
    ("Cement / Construction / Infra", ("CEMENT", "INFRA", "CONSTRUCT", "BUILD", "ENGINEER", "PROJECT", "REALTY",
                                       "ESTATE", "DEVELOPER", "HOUSING", "PROPERT", "GRANITE", "CERAMIC", "TILES",
                                       "ROADS", "HIGHWAY"),
     ("cement", "construction", "infra", "infrastructure", "real estate", "realty", "property", "housing")),
    ("Chemicals / Fertilisers", ("CHEM", "FERTI", "PLASTIC", "POLY", "PAINT", "PESTIC", "AGROCHEM", "AMINES",
                                 "DYES", "RESIN", "SPECIALITY", "ORGANIC", "PETROCHEM", "GASES"),
     ("chemical", "chemicals", "fertiliser", "fertilizer", "paint", "plastic", "polymer")),
    ("FMCG / Food / Consumer", ("FOOD", "FOODS", "BEVERAGE", "AGRO", "SUGAR", "TEA", "COFFEE", "DAIRY", "MILK",
                                "CONSUMER", "BRANDS", "BISCUIT", "SPICE", "RICE", "EDIBLE",
                                "HINDUNILVR", "NESTLE", "BRITANNIA", "DABUR", "MARICO", "DUMS", "TOBACCO", "LIQUOR",
                                "BREWER", "DISTILL", "SNACK"),
     ("fmcg", "food", "consumer", "agri", "agro", "sugar", "beverage", "dairy", "tea")),
    ("Textiles / Apparel", ("TEXTILE", "TEX", "MILLS", "SPINN", "COTTON", "FABRIC", "GARMENT", "APPAREL", "FASHION",
                            "SILK", "YARN", "DENIM", "JUTE", "WEAV"),
     ("textile", "textiles", "apparel", "garment", "fashion", "clothing")),
    ("Defence / Aerospace / Capital Goods", ("DEFENCE", "DEFENSE", "AERO", "SHIPYARD", "SHPG", "SHIP",
                                             "MACHINE", "EQUIPMENT", "ELECTRICALS", "ELECTRONIC",
                                             "CABLES", "TRANSFORMER", "PUMP", "VALVE", "TOOLS", "COMPONENTS"),
     ("defence", "defense", "aerospace", "capital goods", "shipping", "electronics", "electricals", "machinery")),
    ("Logistics / Travel", ("LOGISTIC", "CARRIER", "TRANSPORT", "CARGO", "PORT", "AIRLINE", "AVIATION", "TRAVEL",
                            "COURIER", "SHIPPING", "RAIL", "HOTEL", "RESORT", "TOURISM"),
     ("logistics", "transport", "travel", "aviation", "airline", "hotel", "tourism", "railway", "rail")),
    ("Retail / E-commerce", ("RETAIL", "MART", "STORES", "TRADING", "COMMERCE", "ZOMATO",
                             "NYKAA", "JEWEL", "GOLD", "DIAMOND", "MARKETING"),
     ("retail", "ecommerce", "e-commerce", "jewellery", "jewelry", "trading")),
]

SYMBOL_OVERRIDES = {
    "TCS": "IT / Software", "HCLTECH": "IT / Software", "LTIM": "IT / Software", "TECHM": "IT / Software",
    "ITC": "FMCG / Food / Consumer", "HAL": "Defence / Aerospace / Capital Goods",
    "BEL": "Defence / Aerospace / Capital Goods", "BHEL": "Defence / Aerospace / Capital Goods",
    "RELIANCE": "Power / Energy", "ONGC": "Power / Energy", "LT": "Cement / Construction / Infra",
    "MARUTI": "Auto / EV", "M&M": "Auto / EV", "EICHERMOT": "Auto / EV", "HEROMOTOCO": "Auto / EV",
    "ASIANPAINT": "Chemicals / Fertilisers", "TITAN": "Retail / E-commerce", "DMART": "Retail / E-commerce",
    "BHARTIARTL": "Telecom / Media", "IDEA": "Telecom / Media", "SUNPHARMA": "Pharma / Healthcare",
    "CIPLA": "Pharma / Healthcare", "DRREDDY": "Pharma / Healthcare", "IRCTC": "Logistics / Travel",
    "INDIGO": "Logistics / Travel", "SBIN": "Banking", "KOTAKBANK": "Banking", "AXISBANK": "Banking",
    "HDFCBANK": "Banking", "ICICIBANK": "Banking", "INDUSINDBK": "Banking", "BAJFINANCE": "Finance / NBFC",
    "BAJAJFINSV": "Finance / NBFC", "LICI": "Finance / NBFC", "PAYTM": "Finance / NBFC",
}

SECTOR_NAMES = [s for s, _, _ in SECTOR_RULES] + ["Other / Unclassified"]

EXAMPLE_QUERIES = [
    "penny stocks",
    "high volume penny stocks",
    "banking stocks under 500",
    "pharma gainers",
    "IT stocks between 1000 and 5000",
    "bullish auto stocks",
    "top losers today",
    "most traded stocks",
    "power stocks under 100",
    "5 day gainers metal",
]

_NUM = r"(\d[\d,]*(?:\.\d+)?)\s*(k|thousand)?"


def _num(val: str, suffix: str | None) -> float:
    n = float(val.replace(",", ""))
    return n * 1000 if suffix else n


def infer_sector(name: str | None, symbol: str | None = None) -> str:
    sym = (symbol or "").upper()
    if sym in SYMBOL_OVERRIDES:
        return SYMBOL_OVERRIDES[sym]
    text = f" {(name or '').upper()} {sym} "
    for sector, keys, _ in SECTOR_RULES:
        if any(k in text for k in keys):
            return sector
    return "Other / Unclassified"


def parse_query(query: str) -> dict[str, Any]:
    """Turn plain English into structured filters."""
    q = " " + re.sub(r"[₹rs\.]{0,3}(?=\d)", "", (query or "").lower()).replace("rupees", "").strip() + " "
    q = re.sub(r"\s+", " ", q)
    f: dict[str, Any] = {"min_price": None, "max_price": None, "sectors": [], "sort": None,
                         "direction": None, "min_vol_ratio": None, "limit": 50, "labels": []}

    # Price ranges
    m = re.search(r"between " + _NUM + r" (?:and|to|-) " + _NUM, q) or re.search(_NUM + r" ?(?:to|-) ?" + _NUM, q)
    if m:
        a, b = _num(m.group(1), m.group(2)), _num(m.group(3), m.group(4))
        f["min_price"], f["max_price"] = min(a, b), max(a, b)
    else:
        m = re.search(r"(?:under|below|less than|cheaper than|upto|up to|within|<)\s*" + _NUM, q)
        if m:
            f["max_price"] = _num(m.group(1), m.group(2))
        m = re.search(r"(?:above|over|more than|greater than|>)\s*" + _NUM, q)
        if m:
            f["min_price"] = _num(m.group(1), m.group(2))

    if re.search(r"\bpenny\b|\bcheap\b|\blow price\b|\blow priced\b", q) and f["max_price"] is None:
        f["max_price"] = PENNY_MAX_PRICE
        f["labels"].append(f"Penny stocks (price ≤ ₹{PENNY_MAX_PRICE:g})")
    if re.search(r"\b(expensive|high price|high priced|premium)\b", q) and f["min_price"] is None:
        f["min_price"] = 1000.0

    # Sectors (whole-word alias match)
    for sector, _, aliases in SECTOR_RULES:
        if any(re.search(r"\b" + re.escape(a) + r"\b", q) for a in aliases):
            f["sectors"].append(sector)

    if re.fullmatch(r" it ", q) and "IT / Software" not in f["sectors"]:
        f["sectors"].append("IT / Software")

    # Direction / sort
    five_day = bool(re.search(r"\b(5 ?day|five day|weekly|week)\b", q))
    if re.search(r"\b(gainer|gainers|top gain|rising|up today|winners|green)\b", q):
        f["direction"], f["sort"] = "up", ("r5" if five_day else "r1")
    elif re.search(r"\b(loser|losers|falling|down today|red|crash|fallen)\b", q):
        f["direction"], f["sort"] = "down", ("r5" if five_day else "r1")
    elif re.search(r"\b(bull|bullish|uptrend|momentum|strong)\b", q):
        f["direction"], f["sort"] = "bull", "r5"
    elif re.search(r"\b(bear|bearish|downtrend|weak)\b", q):
        f["direction"], f["sort"] = "bear", "r5"

    if re.search(r"\b(high volume|volume spike|volume breakout|heavy volume|unusual volume)\b", q):
        f["min_vol_ratio"] = 1.5
        f["sort"] = f["sort"] or "vr"
    if re.search(r"\b(most traded|most active|liquid|high turnover|large cap|largecap|blue ?chip)\b", q):
        f["sort"] = "turnover"
        if re.search(r"large ?cap|blue ?chip", q):
            f["labels"].append("Large-cap approximated by highest NSE turnover (market-cap data not in Bhavcopy)")
    if f["sort"] is None and re.search(r"\b(small cap|smallcap|micro cap|microcap)\b", q):
        f["sort"] = "turnover_asc"
        f["labels"].append("Small/micro-cap approximated by lowest NSE turnover")

    m = re.search(r"\btop (\d{1,3})\b|\b(\d{1,3}) stocks\b", q)
    if m:
        f["limit"] = max(1, min(500, int(m.group(1) or m.group(2))))
    if f["sort"] is None:
        f["sort"] = "turnover"
    return f


def describe_filters(f: dict[str, Any]) -> str:
    parts = []
    if f["min_price"] is not None and f["max_price"] is not None:
        parts.append(f"Price ₹{f['min_price']:g} – ₹{f['max_price']:g}")
    elif f["max_price"] is not None:
        parts.append(f"Price ≤ ₹{f['max_price']:g}")
    elif f["min_price"] is not None:
        parts.append(f"Price ≥ ₹{f['min_price']:g}")
    if f["sectors"]:
        parts.append("Sector: " + ", ".join(f["sectors"]))
    names = {"up": "Gainers", "down": "Losers", "bull": "Bullish 5-day trend", "bear": "Bearish 5-day trend"}
    if f["direction"]:
        parts.append(names[f["direction"]])
    if f["min_vol_ratio"]:
        parts.append(f"Volume ≥ {f['min_vol_ratio']}x 5-day average")
    sort_names = {"r1": "1-day % change", "r5": "5-day % change", "vr": "volume spike",
                  "turnover": "highest turnover", "turnover_asc": "lowest turnover"}
    parts.append("Sorted by " + sort_names.get(f["sort"], f["sort"]))
    parts.extend(lbl for lbl in f.get("labels", []) if not lbl.startswith("Penny"))
    return " | ".join(parts)


def screen_stocks(stocks: list[dict], query: str) -> tuple[list[dict], dict[str, Any]]:
    f = parse_query(query)
    out: list[dict] = []
    for r in stocks:
        c = r.get("c")
        if not c:
            continue
        c = float(c)
        if f["min_price"] is not None and c < f["min_price"]:
            continue
        if f["max_price"] is not None and c > f["max_price"]:
            continue
        sector = infer_sector(r.get("n"), r.get("s"))
        if f["sectors"] and sector not in f["sectors"]:
            continue
        if not f["sectors"] and sector == "ETF / Mutual Fund" and "etf" not in (query or "").lower():
            continue  # keep ETFs out of normal stock searches
        r1 = float(r.get("r1") or 0.0)
        r5 = float(r.get("r5") or 0.0)
        up5 = int(r.get("up5") or 0)
        vr = float(r.get("vr") or 0.0)
        d = f["direction"]
        if d == "up" and (r5 if f["sort"] == "r5" else r1) <= 0:
            continue
        if d == "down" and (r5 if f["sort"] == "r5" else r1) >= 0:
            continue
        if d == "bull" and not (r5 > 0 and up5 >= 3):
            continue
        if d == "bear" and not (r5 < 0 and up5 <= 2):
            continue
        if f["min_vol_ratio"] and vr < f["min_vol_ratio"]:
            continue
        turnover_cr = round(c * float(r.get("v") or 0.0) / 1e7, 2)
        out.append({
            "Symbol": r.get("s"),
            "Company": r.get("n"),
            "Sector (inferred)": sector,
            "Price ₹": round(c, 2),
            "1D %": round(r1, 2),
            "5D %": round(r5, 2) if r.get("r5") is not None else None,
            "Up Days (5D)": f"{up5}/{int(r.get('nd') or 5)}",
            "Vol vs 5D Avg": vr,
            "Turnover ₹ Cr": turnover_cr,
            "5D High ₹": r.get("h5"),
            "5D Low ₹": r.get("l5"),
        })
    key_map = {
        "r1": lambda x: x["1D %"], "r5": lambda x: x["5D %"] or 0.0, "vr": lambda x: x["Vol vs 5D Avg"],
        "turnover": lambda x: x["Turnover ₹ Cr"], "turnover_asc": lambda x: x["Turnover ₹ Cr"],
    }
    reverse = not (f["sort"] == "turnover_asc" or f["direction"] in ("down", "bear"))
    out.sort(key=key_map.get(f["sort"], key_map["turnover"]), reverse=reverse)
    f["total_matches"] = len(out)
    return out[: f["limit"]], f
