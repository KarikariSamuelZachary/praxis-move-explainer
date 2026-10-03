'use client';

import { useEffect, useMemo, useState } from 'react';
import dynamic from 'next/dynamic';

import AnalysisPanel from '@/components/review/AnalysisPanel';
import ImportPanel, {
  ImportSource,
} from '@/components/review/ImportPanel';
import ReviewShell from '@/components/review/ReviewShell';
import { GameReviewMove } from '@/types';

import { displayedExplanationFor, lastPlyFor } from './review-page-logic';
import { buildMainlineTree, mainlinePlyToNode } from './review-tree';
import { activeNodeView } from './review-tree-view';

const START_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';

// Same lazy-board pattern as the other app routes: BoardPanel statically
// imports chess.js + react-chessboard, which the empty review page (just an
// import textarea) should not pay for.
const BoardPanel = dynamic(() => import('@/components/review/BoardPanel'), {
  ssr: false,
  loading: () => (
    <div className="mx-auto aspect-square w-full max-w-[calc(100vh-70px)] animate-pulse rounded-[10px] border border-white/10 bg-black/40" />
  ),
});

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
  // Sandbox step 1: the active selection is a tree node id. Variations are
  // not selectable yet, so this only ever points at mainline nodes.
  const [activeNodeId, setActiveNodeId] = useState<string | null>(null);
  const [coachExplanation, setCoachExplanation] = useState<ReviewExplanation | null>(null);
  const [coachError, setCoachError] = useState<string | null>(null);
  const [isAskingCoach, setIsAskingCoach] = useState(false);
  const [showBestMove, setShowBestMove] = useState(false);

  const tree = useMemo(
    () => (gameData ? buildMainlineTree(gameData) : null),
    [gameData],
  );

  useEffect(() => {
    setCoachExplanation(null);
    setCoachError(null);
    setShowBestMove(false);
  }, [activeNodeId]);

  useEffect(() => {
    if (analysisState !== 'ready' || !tree) {
      return;
    }
    setActiveNodeId(tree.mainlineIds[0]);
    setCoachExplanation(null);
    setCoachError(null);
    setShowBestMove(false);
  }, [analysisState, tree]);

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
    if (!view || !currentMove || analysisState !== 'ready') {
      return;
    }

    setIsAskingCoach(true);
    setCoachError(null);
    try {
      const response = await fetch('/api/explain', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          fen: currentMove.fen,
          move: currentMove.san,
          classification: currentMove.classification,
          moveHistory: view.coachHistory.map((entry) => entry.san),
        }),
      });

      if (!response.ok) {
        throw new Error(`Explanation API returned ${response.status}`);
      }

      const data = (await response.json()) as ReviewExplanation;
      setCoachExplanation(data);
    } catch (error) {
      console.error('Failed to fetch review explanation:', error);
      setCoachError('Coach is unavailable right now. Please try again.');
    } finally {
      setIsAskingCoach(false);
    }
  }

  const hasGame = analysisState === 'ready' && gameData !== null;
  const view =
    hasGame && tree && activeNodeId
      ? activeNodeView(tree, activeNodeId, START_FEN)
      : null;
  const currentMove = view?.currentMove ?? null;
  const activePly = view ? tree!.nodes[activeNodeId!].ply : 0;
  const displayedExplanation = displayedExplanationFor(
    currentMove,
    coachExplanation,
  );
  const moveNumberLabel = view?.moveNumberLabel ?? 'Starting position';
  const bestMoveSan = view?.bestMoveSan ?? null;

  function handlePlySelect(ply: number) {
    if (!tree) {
      return;
    }
    const nodeId = mainlinePlyToNode(tree, ply);
    if (nodeId) {
      setActiveNodeId(nodeId);
    }
  }

  return (
    <div className="relative -mt-2 h-[calc(100vh-2.5rem)] w-full overflow-y-auto px-6 pb-[10px] pt-6 text-white lg:overflow-hidden lg:px-10 [background-image:url(/walnut-dark.webp)] [background-size:cover] [background-position:center]">
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
            showBestMove={showBestMove}
          />
        }
        analysisPanel={
          <AnalysisPanel
            currentMove={currentMove}
            hasGame={hasGame}
            explanation={displayedExplanation}
            coachError={coachError}
            isAskingCoach={isAskingCoach}
            onAskCoach={handleAskCoach}
            moveNumberLabel={moveNumberLabel}
            activePly={activePly}
            lastPly={lastPlyFor(gameData ?? [])}
            onPlySelect={handlePlySelect}
            bestMoveSan={bestMoveSan}
            showBestMove={showBestMove}
            onToggleBestMove={() => setShowBestMove((value) => !value)}
          />
        }
      />
    </div>
  );
}
