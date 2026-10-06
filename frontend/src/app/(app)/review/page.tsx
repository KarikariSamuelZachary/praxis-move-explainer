'use client';

import { useEffect, useState } from 'react';
import dynamic from 'next/dynamic';
import { Chess } from 'chess.js';

import AnalysisPanel from '@/components/review/AnalysisPanel';
import ImportPanel, {
  ImportSource,
} from '@/components/review/ImportPanel';
import ReviewShell from '@/components/review/ReviewShell';
import {
  GameReviewMove,
  ReviewCapabilities,
  SandboxMoveResponse,
} from '@/types';

import { displayedExplanationFor, lastPlyFor } from './review-page-logic';
import {
  ReviewTree,
  addVariation,
  buildMainlineTree,
  mainlinePlyToNode,
  pathSans,
  removeLeafNode,
  setNodeAnalysis,
  setNodeMove,
} from './review-tree';
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

type AnalysisProgress = { done: number; total: number | null };

type ReviewStreamMessage =
  | { type: 'meta'; mode: string; total: number }
  | { type: 'row'; row: GameReviewMove }
  | { type: 'done' }
  | { type: 'error'; detail?: string };

type ResolvedExploreMove = {
  uci: string;
  san: string;
  fen: string;
  color: 'white' | 'black';
};

/** Legal move for a drag/click on `fen`; promotions default to a queen. */
function resolveExploreMove(
  fen: string,
  from: string,
  to: string,
  promotion?: string,
): ResolvedExploreMove | null {
  try {
    const chess = new Chess(fen);
    const candidates = chess
      .moves({ verbose: true })
      .filter((move) => move.from === from && move.to === to);
    if (candidates.length === 0) {
      return null;
    }
    const chosen = promotion
      ? candidates.find((move) => move.promotion === promotion)
      : candidates[0];
    if (!chosen) {
      return null;
    }
    const played = chess.move({ from, to, promotion: chosen.promotion });
    return {
      uci: `${from}${to}${chosen.promotion ?? ''}`,
      san: played.san,
      fen: chess.fen(),
      color: played.color === 'w' ? 'white' : 'black',
    };
  } catch {
    return null;
  }
}

export default function ReviewPage() {
  const [pgnInput, setPgnInput] = useState('');
  const [importSource, setImportSource] = useState<ImportSource>('paste');
  const [analysisState, setAnalysisState] = useState<AnalysisState>('idle');
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [gameData, setGameData] = useState<GameReviewMove[] | null>(null);
  const [tree, setTree] = useState<ReviewTree | null>(null);
  const [activeNodeId, setActiveNodeId] = useState<string | null>(null);
  const [coachExplanation, setCoachExplanation] = useState<ReviewExplanation | null>(null);
  const [coachError, setCoachError] = useState<string | null>(null);
  const [isAskingCoach, setIsAskingCoach] = useState(false);
  const [showBestMove, setShowBestMove] = useState(false);
  const [sandboxEnabled, setSandboxEnabled] = useState(false);
  const [exploreMode, setExploreMode] = useState(false);
  const [exploreError, setExploreError] = useState<string | null>(null);
  const [analysisProgress, setAnalysisProgress] =
    useState<AnalysisProgress | null>(null);
  // Mode fingerprint of the finished review (stream meta line). Echoed back
  // as expected_mode so the sandbox rejects explores against a stale review.
  const [reviewMode, setReviewMode] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch('/api/review/capabilities')
      .then((response) => (response.ok ? response.json() : null))
      .then((data: ReviewCapabilities | null) => {
        if (!cancelled && data) {
          setSandboxEnabled(Boolean(data.sandbox_enabled));
        }
      })
      .catch(() => {
        // Sandbox stays hidden when capabilities are unavailable.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    setCoachExplanation(null);
    setCoachError(null);
    setShowBestMove(false);
  }, [activeNodeId]);

  useEffect(() => {
    if (analysisState !== 'ready' || !gameData) {
      return;
    }
    const built = buildMainlineTree(gameData);
    setTree(built);
    setActiveNodeId(built.mainlineIds[0]);
    setExploreMode(false);
    setExploreError(null);
    setCoachExplanation(null);
    setCoachError(null);
    setShowBestMove(false);
  }, [analysisState, gameData]);

  const canAnalyze = pgnInput.trim().length > 0 && analysisState !== 'analyzing';

  async function handleAnalyzeGame() {
    if (!canAnalyze) {
      return;
    }

    setErrorMessage(null);
    setAnalysisState('analyzing');
    setAnalysisProgress({ done: 0, total: null });
    setReviewMode(null);

    try {
      const response = await fetch('/api/analyze', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/x-ndjson',
        },
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
      if (!response.body) {
        throw new Error('Analyze API returned no review stream');
      }

      // The backend streams one NDJSON line per completed ply; parse complete
      // lines as they arrive so the progress count moves during the review.
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      const rows: GameReviewMove[] = [];
      let buffer = '';

      const handleLine = (line: string) => {
        const trimmed = line.trim();
        if (!trimmed) {
          return;
        }
        const message = JSON.parse(trimmed) as ReviewStreamMessage;
        if (message.type === 'meta') {
          setAnalysisProgress({ done: 0, total: message.total });
          setReviewMode(message.mode);
        } else if (message.type === 'row') {
          rows.push(message.row);
          setAnalysisProgress((current) => ({
            done: rows.length,
            total: current?.total ?? null,
          }));
        } else if (message.type === 'error') {
          throw new Error(
            message.detail ?? 'The analysis service could not process this game.',
          );
        }
      };

      for (;;) {
        const { done, value } = await reader.read();
        if (done) {
          break;
        }
        buffer += decoder.decode(value, { stream: true });
        let newline = buffer.indexOf('\n');
        while (newline >= 0) {
          handleLine(buffer.slice(0, newline));
          buffer = buffer.slice(newline + 1);
          newline = buffer.indexOf('\n');
        }
      }
      buffer += decoder.decode();
      handleLine(buffer);

      if (rows.length === 0) {
        throw new Error('Analyze API returned no review data');
      }

      setGameData(rows);
      setAnalysisState('ready');
    } catch (error) {
      const detail = error instanceof Error ? error.message : 'Please check the format and try again.';
      setErrorMessage(`Failed to analyze PGN. ${detail}`);
      setAnalysisState('error');
    } finally {
      setAnalysisProgress(null);
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

  async function handleExploreMove(
    from: string,
    to: string,
    promotion?: string,
  ) {
    if (!exploreMode || !tree || !activeNodeId || !view) {
      return;
    }
    const resolved = resolveExploreMove(view.position, from, to, promotion);
    if (!resolved) {
      return;
    }

    // Optimistic, chess.com-style feedback: append the move immediately with
    // a provisional "Good" label so the piece and its badge land in the same
    // frame; the engine's real label replaces it when the live result
    // arrives (which is why the badge can visibly change afterwards).
    const parentId = activeNodeId;
    const optimisticRow: GameReviewMove = {
      fen: resolved.fen,
      san: resolved.san,
      color: resolved.color,
      classification: 'good',
      cp_loss: 0,
      ep_loss: 0,
      eval_cp: view.currentMove?.eval_cp ?? 0,
      eval_mate: null,
      fen_before: view.position,
      raw_ep_loss: 0,
      second_best_cp: null,
      second_best_move_san: null,
      second_best_move_uci: null,
      second_best_pv_uci: [],
    };
    const { tree: optimisticTree, nodeId } = addVariation(
      tree,
      parentId,
      optimisticRow,
      { status: 'analyzing' },
    );
    const createdOptimisticNode =
      optimisticTree.nodes[nodeId]?.move === optimisticRow;
    setTree(optimisticTree);
    setActiveNodeId(nodeId);
    setExploreError(null);

    try {
      // The sandbox replays the move path from the game start (book
      // contiguity, repetition history, previous-ply context); a bare FEN
      // carries none of that and is rejected, so always send the path.
      const response = await fetch('/api/review/live', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          moves: pathSans(tree, parentId),
          move: resolved.uci,
          player_rating: null,
          expected_mode: reviewMode,
        }),
      });

      if (!response.ok) {
        let detail = `Live analysis returned ${response.status}`;
        try {
          const errorBody = (await response.json()) as AnalyzeErrorResponse;
          detail = errorBody.detail ?? errorBody.error ?? detail;
        } catch {
          // Keep the status-based message.
        }
        throw new Error(detail);
      }

      const data = (await response.json()) as SandboxMoveResponse;
      const row: GameReviewMove = {
        fen: data.fen,
        san: data.move_san,
        color: data.color,
        classification: data.classification,
        cp_loss: data.cp_loss,
        ep_loss: data.ep_loss,
        eval_cp: data.eval_cp,
        eval_mate: data.eval_mate ?? null,
        best_move_san: data.best?.move_san ?? null,
        best_move_uci: data.best?.move_uci ?? null,
        fen_before: data.fen_before,
        raw_ep_loss: data.ep_loss,
        second_best_cp: data.second_best?.eval_cp ?? null,
        second_best_move_san: data.second_best?.move_san ?? null,
        second_best_move_uci: data.second_best?.move_uci ?? null,
        second_best_pv_uci: data.second_best?.pv_uci ?? [],
      };
      setTree((current) => {
        if (!current) {
          return current;
        }
        const withMove = setNodeMove(current, nodeId, row);
        return setNodeAnalysis(withMove, nodeId, {
          status: 'ready',
          mode: data.mode,
          evalCp: data.eval_cp,
          evalMate: data.eval_mate ?? null,
          classification: data.classification,
          bestMoveUci: data.best?.move_uci ?? null,
        });
      });
    } catch (error) {
      const detail =
        error instanceof Error ? error.message : 'Could not analyze that move.';
      setExploreError(detail);
      // Roll the optimistic node back when nothing branched from it yet;
      // a re-played existing variation keeps its last good label instead.
      if (createdOptimisticNode) {
        setTree((current) =>
          current ? removeLeafNode(current, nodeId) : current,
        );
        setActiveNodeId((current) => (current === nodeId ? parentId : current));
      }
    }
  }

  const hasGame = analysisState === 'ready' && gameData !== null;
  const view =
    hasGame && tree && activeNodeId
      ? activeNodeView(tree, activeNodeId, START_FEN)
      : null;
  const currentMove = view?.currentMove ?? null;
  const activePly = view && tree && activeNodeId ? tree.nodes[activeNodeId].ply : 0;
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
            progress={analysisProgress}
          />
        }
        boardPanel={
          <BoardPanel
            position={view?.position ?? START_FEN}
            fenBefore={view?.fenBefore ?? null}
            currentMove={currentMove}
            isAnalyzing={analysisState === 'analyzing'}
            hasGame={hasGame}
            showBestMove={showBestMove}
            allowDragging={exploreMode}
            onExploreMove={handleExploreMove}
            exploreMode={exploreMode}
            onToggleExplore={() => setExploreMode((value) => !value)}
            sandboxEnabled={sandboxEnabled}
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
            exploreMode={exploreMode}
            exploreError={exploreError}
          />
        }
      />
    </div>
  );
}
