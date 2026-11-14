"""Canonical metric names shared by sourced-evidence import and analysis."""

FUNDAMENTAL_METRICS = {
    "revenue_growth_yoy": "Revenue growth (YoY)",
    "profit_growth_yoy": "Net profit growth (YoY)",
    "operating_margin": "Operating margin",
    "ebitda_margin": "EBITDA margin",
    "net_profit_margin": "Net profit margin",
    "roe": "Return on equity (ROE)",
    "roce": "Return on capital employed (ROCE)",
    "debt_to_equity": "Debt-to-equity",
    "operating_cash_flow": "Operating cash flow",
    "free_cash_flow": "Free cash flow",
    "promoter_holding": "Promoter holding",
    "promoter_pledging": "Promoter shares pledged",
    "institutional_holding": "Institutional holding",
    "pe_ratio": "Price-to-earnings (P/E)",
    "pb_ratio": "Price-to-book (P/B)",
    "ps_ratio": "Price-to-sales (P/S)",
    "ev_to_ebitda": "EV/EBITDA",
    "sector_valuation": "Sector valuation reference",
    "eps_growth_yoy": "EPS growth (YoY)",
}

# Inputs that may be recorded in manually sourced evidence. Component facts are
# kept separate from calculated output metrics so every calculation can cite
# the exact source records used.
FUNDAMENTAL_FACT_KEYS = frozenset(FUNDAMENTAL_METRICS) | frozenset({
    "revenue",
    "net_profit",
    "operating_profit",
    "ebitda",
    "ebit",
    "shareholders_equity",
    "average_shareholders_equity",
    "total_debt",
    "capital_employed",
    "average_capital_employed",
    "capital_expenditure",
    "eps",
})

FUNDAMENTAL_DATA_STATUSES = ("verified", "unverified", "conflicting", "missing", "synthetic")
FINANCIAL_BASES = ("standalone", "consolidated", "unspecified")

