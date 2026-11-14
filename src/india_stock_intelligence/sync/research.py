from __future__ import annotations


class OrchestratorResearchTrigger:
    """Connect new, validated price rows to the existing research orchestrator.

    A caller must provide an already-approved backend. This adapter does not
    construct an OpenAI client, make API calls, or select a model.
    """

    def __init__(self, orchestrator) -> None:
        self.orchestrator = orchestrator

    def __call__(self, security_keys: list[str], as_of_date: str):
        results = self.orchestrator.run_universe(
            security_keys, as_of_date=as_of_date, sample_only=False
        )
        if any(getattr(result, "status", None) != "completed" for result in results):
            raise RuntimeError("One or more existing research runs did not complete successfully")
        return results
