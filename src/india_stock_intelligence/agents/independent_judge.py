from .base import ResearchAgent


class IndependentJudge(ResearchAgent):
    agent_id = "independent_judge"
    prompt_file = "independent_judge.md"
