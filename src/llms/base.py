from abc import abstractmethod, ABC
from schemas.models import Mistake, Explanation

# Review explanations run sequentially, one provider call per mistake, so a
# single hung request stalls the whole review. Keep provider calls strictly
# bounded and retries minimal (defaults are 60s x 3 for Groq/OpenAI and
# ~5 attempts with up to 60s backoff for Gemini).
LLM_TIMEOUT_SECONDS = 15
LLM_MAX_RETRIES = 1


class LLMExplainer(ABC):

    @abstractmethod
    def explain_mistake(self, mistake:Mistake) -> Explanation:
        pass