"""Mock LLM explainer for testing without API calls."""
from llms.base import LLMExplainer
from schemas.models import Mistake, Explanation


class MockExplainer(LLMExplainer):
    """Mock explainer that returns hardcoded explanations for testing."""
    
    def explain_mistake(self, mistake: Mistake) -> Explanation:
        """Return a mock explanation."""
        return Explanation(
            why_good=f"[Mock] The move {mistake.move_played} appeared to develop your pieces and control the center.",
            why_failed=f"[Mock] This move ignores a critical tactical threat. The best move was {mistake.evaluation_before.best_move_san}.",
            concept_involved="[Mock] Tactical awareness and threat recognition",
            typical_pattern="[Mock] Always check for opponent's threats (checks, captures, attacks) before making your move."
        )
