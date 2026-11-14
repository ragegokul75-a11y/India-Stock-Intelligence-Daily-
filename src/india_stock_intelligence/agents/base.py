from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files

from ..fundamentals import FundamentalAnalyzer
from .schemas import get_schema
from .validation import validate_agent_output


@dataclass(frozen=True)
class AgentExecution:
    agent_id: str
    output: dict
    request_id: str | None
    model: str | None


class ResearchAgent:
    agent_id: str
    prompt_file: str

    def __init__(self, backend):
        self.backend = backend

    def run(self, bundle, context: dict | None = None) -> AgentExecution:
        prompt = files("india_stock_intelligence").joinpath("prompts", self.prompt_file).read_text(encoding="utf-8")
        fundamental_snapshot = FundamentalAnalyzer().analyze(bundle).to_dict()
        payload = {"evidence_bundle": {**bundle.payload(), "evidence_digest": bundle.digest,
                                       "fundamental_snapshot": fundamental_snapshot},
                   "additional_context": context or {}}
        schema = get_schema(self.agent_id)
        response = self.backend.generate_json(agent_id=self.agent_id, prompt=prompt, payload=payload, schema=schema)
        raw_output = getattr(response, "output", response)
        output = validate_agent_output(raw_output, agent_id=self.agent_id, bundle=bundle, schema=schema)
        return AgentExecution(self.agent_id, output, getattr(response, "request_id", None), getattr(response, "model", None))
