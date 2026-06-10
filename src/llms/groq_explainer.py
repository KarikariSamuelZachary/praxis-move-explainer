import logging

from groq import Groq

from llms.base import LLMExplainer
from llms.mock_explainer import MockExplainer
from schemas.models import Mistake, Explanation

log = logging.getLogger(__name__)


class GroqExplainer(LLMExplainer):
    def __init__(self, api_key, model="openai/gpt-oss-120b"):
        self.api_key = api_key
        self.model = model
        self.client = Groq(api_key=api_key)
        self.fallback_explainer = MockExplainer()

    def explain_mistake(self, mistake: Mistake) -> Explanation:
        prompt = self._build_prompt(mistake)
        log.info("Groq prompt: %s", prompt)
        try:
            response = self._call_groq(prompt)
            explanation = self._parse_response(response)
            return explanation
        except Exception as e:
            # Keep the review usable when the provider rejects a request.
            log.error("GroqExplainer failed, falling back to mock: %s", e)
            return self.fallback_explainer.explain_mistake(mistake)

    def _build_prompt(self, mistake: Mistake) -> str:
        move_num = mistake.position_before_move.move_number
        color = mistake.position_before_move.player_color
        fen_before = mistake.position_before_move.fen
        move_played = mistake.move_played
        best_move = mistake.evaluation_before.best_move_san
        eval_before = mistake.evaluation_before.score_cp / 100
        eval_after = mistake.evaluation_after.score_cp / 100
        eval_drop = mistake.eval_drop_cp / 100
        classification = "blunder" if mistake.eval_drop_cp > 300 else "mistake"

        prompt = f"""You are a chess coach explaining a mistake to a student.

Move {move_num} ({color} to move)
Position before the move (FEN): {fen_before}
Classification: {classification}
Played move (SAN): {move_played}
Best move (SAN): {best_move}
Evaluation: {eval_before:.1f} -> {eval_after:.1f} pawns
Eval drop: {eval_drop:.1f} pawns

Give a specific 2-3 sentence explanation of why the played move is bad in this exact board position and what the best move achieves. Use concrete chess details from the FEN, played move, and best move.

Respond using this exact structure:

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

    def _call_groq(self, prompt: str) -> str:
        completion = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            temperature=0.3,
            max_completion_tokens=220,
            top_p=1,
            reasoning_effort="low",
            stream=False,
        )
        return completion.choices[0].message.content or ""

    def _parse_response(self, response: str) -> Explanation:
        sections = {
            "why_looked_good": "",
            "why_failed": "",
            "concept_involved": "",
            "typical_pattern": "",
        }

        lines = response.strip().split("\n")
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
            typical_pattern=sections["typical_pattern"],
        )
