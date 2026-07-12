'use client';

import { useEffect, useState } from 'react';

import AnalysisPanel from '@/components/review/AnalysisPanel';
import BoardPanel from '@/components/review/BoardPanel';
import ImportPanel, {
  ImportSource,
} from '@/components/review/ImportPanel';
import ReviewShell from '@/components/review/ReviewShell';
import { GameReviewMove } from '@/types';

type AnalysisState = 'idle' | 'analyzing' | 'ready' | 'error';

type ReviewExplanation = NonNullable<GameReviewMove['explanation']>;

type AnalyzeErrorResponse = {
  detail?: string;
  error?: string;
};

export default function ReviewPage() {
  const [pgnInput, setPgnInput] = useState('');
  const [importSource, setImportSource] = useState<ImportSource>('paste');
  const [analysisState, setAnalysisState] = useState<AnalysisState>('idle');
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [gameData, setGameData] = useState<GameReviewMove[] | null>(null);
  const [activePly, setActivePly] = useState(0);
  const [coachExplanation, setCoachExplanation] = useState<ReviewExplanation | null>(null);
  const [isAskingCoach, setIsAskingCoach] = useState(false);

  useEffect(() => {
    setCoachExplanation(null);
  }, [activePly]);

  useEffect(() => {
    if (analysisState !== 'ready' || !gameData) {
      return;
    }
    setActivePly(0);
    setCoachExplanation(null);
  }, [analysisState, gameData]);

  const canAnalyze = pgnInput.trim().length > 0 && analysisState !== 'analyzing';

  async function handleAnalyzeGame() {
    if (!canAnalyze) {
      return;
    }

    setErrorMessage(null);
    setAnalysisState('analyzing');

    try {
      const response = await fetch('/api/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pgn: pgnInput.trim() }),
      });

      if (!response.ok) {
        let detail = `Analyze API returned ${response.status}`;
        try {
          const errorBody = (await response.json()) as AnalyzeErrorResponse;
          detail = errorBody.detail ?? errorBody.error ?? detail;
        } catch {
          // Keep the status-based message when the response is not JSON.
        }
        throw new Error(detail);
      }

      const data = (await response.json()) as GameReviewMove[];
      if (!Array.isArray(data) || data.length === 0) {
        throw new Error('Analyze API returned no review data');
      }

      setGameData(data);
      setAnalysisState('ready');
    } catch (error) {
      const detail = error instanceof Error ? error.message : 'Please check the format and try again.';
      setErrorMessage(`Failed to analyze PGN. ${detail}`);
      setAnalysisState('error');
    }
  }

  async function handleAskCoach() {
    if (!gameData || analysisState !== 'ready') {
      return;
    }
    const move = gameData[Math.min(activePly, gameData.length - 1)];
    if (!move) {
      return;
    }

    setIsAskingCoach(true);
    try {
      const response = await fetch('/api/explain', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          fen: move.fen,
          move: move.san,
          classification: move.classification,
          moveHistory: gameData.slice(0, activePly + 1).map((entry) => entry.san),
        }),
      });

      if (!response.ok) {
        throw new Error(`Explanation API returned ${response.status}`);
      }

      const data = (await response.json()) as ReviewExplanation;
      setCoachExplanation(data);
    } catch (error) {
      console.error('Failed to fetch review explanation:', error);
    } finally {
      setIsAskingCoach(false);
    }
  }

  const hasGame = analysisState === 'ready' && gameData !== null;
  const currentMove = hasGame && gameData ? gameData[Math.min(activePly, gameData.length - 1)] : null;
  const displayedExplanation = currentMove?.explanation ?? coachExplanation;
  const moveNumberLabel = formatMoveNumber(activePly);

  return (
    <div className="relative -mt-2 h-[calc(100vh-2.25rem)] w-full overflow-y-auto px-6 pb-[10px] pt-6 text-white lg:overflow-hidden lg:px-10 [background-image:url(/walnut-dark.png)] [background-size:cover] [background-position:center]">
      <ReviewShell
        importPanel={
          <ImportPanel
            pgn={pgnInput}
            onPgnChange={setPgnInput}
            source={importSource}
            onSourceChange={setImportSource}
            onImport={handleAnalyzeGame}
            isAnalyzing={analysisState === 'analyzing'}
            errorMessage={analysisState === 'error' ? errorMessage : null}
            disabled={!hasGame && analysisState === 'analyzing'}
          />
        }
        boardPanel={
          <BoardPanel
            moves={gameData ?? []}
            activePly={activePly}
            isAnalyzing={analysisState === 'analyzing'}
            hasGame={hasGame}
          />
        }
        analysisPanel={
          <AnalysisPanel
            currentMove={currentMove}
            hasGame={hasGame}
            explanation={displayedExplanation}
            isAskingCoach={isAskingCoach}
            onAskCoach={handleAskCoach}
            moveNumberLabel={moveNumberLabel}
            activePly={activePly}
            lastPly={Math.max(0, (gameData?.length ?? 0) - 1)}
            onPlySelect={setActivePly}
          />
        }
      />
    </div>
  );
}

function formatMoveNumber(activePly: number): string {
  if (activePly === 0) {
    return 'Starting position';
  }
  const moveIndex = activePly - 1;
  const fullMove = Math.floor(moveIndex / 2) + 1;
  const suffix = moveIndex % 2 === 0 ? 'White' : 'Black';
  return `Move ${fullMove} · ${suffix}`;
}
