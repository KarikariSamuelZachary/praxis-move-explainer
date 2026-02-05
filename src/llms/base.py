from abc import abstractmethod, ABC
from schemas.models import Mistake, Explanation

class LLMExplainer(ABC):

    @abstractmethod
    def explain_mistake(self, mistake:Mistake) -> Explanation:
        pass