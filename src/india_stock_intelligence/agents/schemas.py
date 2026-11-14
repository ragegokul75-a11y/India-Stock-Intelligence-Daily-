from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files


@lru_cache(maxsize=None)
def get_schema(agent_id: str) -> dict:
    allowed = {"equity_research_analyst", "bull_bear_analyst", "independent_judge"}
    if agent_id not in allowed:
        raise ValueError(f"Unknown agent schema: {agent_id}")
    path = files("india_stock_intelligence").joinpath("schemas", f"{agent_id}.json")
    return json.loads(path.read_text(encoding="utf-8"))
