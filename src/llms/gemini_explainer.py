import logging

from llms.base import LLMExplainer, LLM_MAX_RETRIES, LLM_TIMEOUT_SECONDS
from llms.mock_explainer import MockExplainer
from schemas.models import Mistake, Explanation
from google import genai
from google.genai import types as genai_types

log = logging.getLogger(__name__)


class GeminiExplainer(LLMExplainer):
    def __init__(self, api_key, model='gemini-2.0-flash'):
        self.api_key = api_key
        self.model_name = model
        self.client = genai.Client(
            api_key=api_key,
            http_options=genai_types.HttpOptions(
                timeout=LLM_TIMEOUT_SECONDS * 1000,  # this SDK takes milliseconds
                retry_options=genai_types.HttpRetryOptions(
                    attempts=LLM_MAX_RETRIES + 1,  # this SDK counts total attempts
                ),
            ),
        )
        self.fallback_explainer = MockExplainer()

    def explain_mistake(self, mistake: Mistake) -> Explanation:
        prompt = self._build_prompt(mistake)
        try:
            response = self._call_gemini(prompt)
            explanation = self._parse_response(response)
            return explanation
        except Exception as e:
            # Keep the review usable when the provider rejects or stalls.
            log.error("GeminiExplainer failed, falling back to mock: %s", e)
            return self.fallback_explainer.explain_mistake(mistake)

    def _build_prompt(self, mistake: Mistake) -> str:
        move_num = mistake.position_before_move.move_number
        color = mistake.position_before_move.player_color
        move_played = mistake.move_played
        best_move = mistake.evaluation_before.best_move_san
        eval_before = mistake.evaluation_before.score_cp / 100
        eval_after = mistake.evaluation_after.score_cp / 100
        eval_drop = mistake.eval_drop_cp / 100
        
        prompt = f"""You are a chess coach explaining a mistake to a student.

Move {move_num} ({color} to move)
Move played: {move_played}
Best move: {best_move}
Evaluation: {eval_before:.1f} → {eval_after:.1f} pawns
Drop: {eval_drop:.1f} pawns

Explain this mistake concisely using this exact structure:

WHY IT LOOKED GOOD:
[One sentence about what the player was trying to accomplish]

WHY IT FAILED:
[1-2 sentences about the tactical or strategic problem]

CONCEPT:
[One phrase naming the chess principle violated, e.g., 'King safety' or 'Piece coordination']

PATTERN:
[One sentence about the general pattern to recognize in similar positions]

Be direct and educational. Avoid engine terminology."""
        
        return prompt

    def _call_gemini(self, prompt: str) -> str:
        response = self.client.models.generate_content(
            model=self.model_name,
            contents=prompt
        )
        return response.text

    def _parse_response(self, response: str) -> Explanation:
        sections = {
            "why_looked_good": "",
            "why_failed": "",
            "concept_involved": "",
            "typical_pattern": ""
        }
        
        lines = response.strip().split('\n')
        current_section = None
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            if "WHY IT LOOKED GOOD" in line.upper():
                current_section = "why_looked_good"
            elif "WHY IT FAILED" in line.upper():
                current_section = "why_failed"
            elif "CONCEPT" in line.upper():
                current_section = "concept_involved"
            elif "PATTERN" in line.upper():
                current_section = "typical_pattern"
            elif current_section:
                if sections[current_section]:
                    sections[current_section] += " " + line
                else:
                    sections[current_section] = line
        
        return Explanation(
            why_good=sections["why_looked_good"],
            why_failed=sections["why_failed"],
            concept_involved=sections["concept_involved"],
            typical_pattern=sections["typical_pattern"]
        )
