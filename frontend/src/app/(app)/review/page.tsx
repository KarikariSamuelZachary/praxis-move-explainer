'use client';

import { useEffect, useState } from 'react';

import AnalysisPanel from '@/components/review/AnalysisPanel';
import BoardPanel from '@/components/review/BoardPanel';
import ImportPanel, {
  ImportExample,
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

function deriveTitleFromPgn(pgn: string, fallback = 'Imported Game'): string {
  const eventMatch = pgn.match(/\[Event\s+"([^"]+)"\]/i);
  if (eventMatch && eventMatch[1] && eventMatch[1].toLowerCase() !== '?') {
    return eventMatch[1];
  }
  const whiteMatch = pgn.match(/\[White\s+"([^"]+)"\]/i);
  const blackMatch = pgn.match(/\[Black\s+"([^"]+)"\]/i);
  if (whiteMatch && blackMatch) {
    return `${whiteMatch[1]} vs. ${blackMatch[1]}`;
  }
  return fallback;
}

export default function ReviewPage() {
  const [pgnInput, setPgnInput] = useState('');
  const [importSource, setImportSource] = useState<ImportSource>('paste');
  const [analysisState, setAnalysisState] = useState<AnalysisState>('idle');
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [gameData, setGameData] = useState<GameReviewMove[] | null>(null);
  const [gameTitle, setGameTitle] = useState('');
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
      setGameTitle(deriveTitleFromPgn(pgnInput));
      setAnalysisState('ready');
    } catch (error) {
      const detail = error instanceof Error ? error.message : 'Please check the format and try again.';
      setErrorMessage(`Failed to analyze PGN. ${detail}`);
      setAnalysisState('error');
    }
  }

  function handleLoadExample(example: ImportExample) {
    setPgnInput(example.pgn);
    setImportSource('paste');
    setErrorMessage(null);
    setAnalysisState('idle');
  }

  function handleClearPgn() {
    setPgnInput('');
    setErrorMessage(null);
    setAnalysisState(gameData ? 'ready' : 'idle');
  }

  function handleNewGame() {
    setGameData(null);
    setGameTitle('');
    setActivePly(0);
    setCoachExplanation(null);
    setErrorMessage(null);
    setAnalysisState('idle');
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
    <main className="h-full w-full overflow-y-auto p-3 lg:h-full lg:overflow-hidden lg:p-4 xl:p-5">
      <ReviewShell
        importPanel={
          <ImportPanel
            pgn={pgnInput}
            onPgnChange={setPgnInput}
            source={importSource}
            onSourceChange={setImportSource}
            onImport={handleAnalyzeGame}
            onLoadExample={handleLoadExample}
            isAnalyzing={analysisState === 'analyzing'}
            errorMessage={analysisState === 'error' ? errorMessage : null}
            onClear={handleClearPgn}
            disabled={!hasGame && analysisState === 'analyzing'}
          />
        }
        boardPanel={
          <BoardPanel
            title={gameTitle}
            onTitleChange={setGameTitle}
            moves={gameData ?? []}
            activePly={activePly}
            onPlySelect={setActivePly}
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
            plyIndex={activePly}
            totalMoves={gameData?.length ?? 0}
          />
        }
      />

      {hasGame && (
        <div className="pointer-events-none absolute bottom-4 left-1/2 z-20 -translate-x-1/2">
          <button
            type="button"
            onClick={handleNewGame}
            className="pointer-events-auto inline-flex items-center gap-1.5 rounded-full border border-amber-900/50 bg-black/70 px-3 py-1.5 text-xs text-zinc-200 shadow-lg shadow-black/50 transition hover:border-emerald-500/40 hover:text-emerald-200"
          >
            <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" viewBox="0 0 24 24" aria-hidden>
              <path d="M12 5v14" />
              <path d="M5 12h14" />
            </svg>
            Import Another Game
          </button>
        </div>
      )}
    </main>
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
