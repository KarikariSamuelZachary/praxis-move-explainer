'use client';

import { useEffect, useRef, useState } from 'react';
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
  SandboxLine,
  SandboxPrewarmResponse,
  SandboxStreamMessage,
} from '@/types';

import { displayedExplanationFor, lastPlyFor } from './review-page-logic';
import {
  ReviewTree,
  SuggestionLine,
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

/** One engine line in the tree's suggestion shape (empty moves dropped). */
function toSuggestionLine(line: SandboxLine): SuggestionLine {
  return {
    moveUci: line.move_uci ?? '',
    moveSan: line.move_san ?? '',
    evalCp: line.eval_cp ?? undefined,
    evalMate: line.eval_mate ?? undefined,
    pvSan: line.pv_san,
  };
}

function suggestionLinesFrom(
  lines: Array<SandboxLine | null | undefined>,
): SuggestionLine[] {
  return lines
    .filter((line): line is SandboxLine => Boolean(line && line.move_uci))
    .map(toSuggestionLine);
}

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
  // In-flight deepening stream; a new explored move aborts the previous one.
  const streamAbortRef = useRef<AbortController | null>(null);

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

  // Explore-mode suggestion: ask for the engine's best move at the active
  // position (the side to move) and draw it as an arrow. Debounced ~300ms
  // after the last selection change; a new selection cancels the pending
  // timer and aborts the in-flight request. Nodes an explore already
  // labelled carry their own line, so they are skipped.
  useEffect(() => {
    if (!exploreMode || !sandboxEnabled || !tree || !activeNodeId) {
      return;
    }
    const node = tree.nodes[activeNodeId];
    if (!node) {
      return;
    }
    if (
      node.analysis?.status === 'analyzing' ||
      node.analysis?.suggestionsAfter?.length
    ) {
      return;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => {
      fetch('/api/review/live/prewarm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          moves: pathSans(tree, activeNodeId),
          expected_mode: reviewMode,
        }),
        signal: controller.signal,
      })
        .then((response) => (response.ok ? response.json() : null))
        .then((data: SandboxPrewarmResponse | null) => {
          if (!data) {
            return;
          }
          const lines = suggestionLinesFrom([data.best, data.second_best]);
          if (lines.length === 0) {
            return;
          }
          setTree((current) => {
            const currentNode = current?.nodes[activeNodeId];
            if (!current || !currentNode) {
              return current;
            }
            return setNodeAnalysis(current, activeNodeId, {
              ...currentNode.analysis,
              suggestionsAfter: lines,
            });
          });
        })
        .catch(() => {
          // Prewarming is best-effort; explores work without it.
        });
    }, 300);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [exploreMode, sandboxEnabled, activeNodeId, reviewMode]); // eslint-disable-line react-hooks/exhaustive-deps

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

    // A new move supersedes any deepening still in flight.
    streamAbortRef.current?.abort();
    const controller = new AbortController();
    streamAbortRef.current = controller;

    // The move lands on the board immediately with no label: the first
    // engine snapshot (well under a second) fills in the eval bar, arrow
    // and a provisional badge; later depths refine them; the final line
    // settles the label at the live depth. No fake "good" in between.
    const parentId = activeNodeId;
    const pendingRow: GameReviewMove = {
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
    const { tree: pendingTree, nodeId } = addVariation(
      tree,
      parentId,
      pendingRow,
      { status: 'analyzing' },
    );
    const createdPendingNode = pendingTree.nodes[nodeId]?.move === pendingRow;
    setTree(pendingTree);
    setActiveNodeId(nodeId);
    setExploreError(null);

    try {
      // The sandbox replays the move path from the game start (book
      // contiguity, repetition history, previous-ply context); a bare FEN
      // carries none of that and is rejected, so always send the path.
      const response = await fetch('/api/review/live/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/x-ndjson',
        },
        body: JSON.stringify({
          moves: pathSans(tree, parentId),
          move: resolved.uci,
          player_rating: null,
          expected_mode: reviewMode,
        }),
        signal: controller.signal,
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
      if (!response.body) {
        throw new Error('Live analysis returned no stream');
      }

      const handleMessage = (message: SandboxStreamMessage) => {
        if (message.type === 'info') {
          // One deepening snapshot: update the provisional badge, eval bar
          // and the next-mover arrow without waiting for the final depth.
          const afterLines = suggestionLinesFrom([message.best_after]);
          setTree((current) => {
            const node = current?.nodes[nodeId];
            if (!current || !node) {
              return current;
            }
            const row: GameReviewMove = {
              ...(node.move ?? pendingRow),
              classification: message.classification,
              cp_loss: message.cp_loss,
              ep_loss: message.ep_loss,
              eval_cp: message.eval_cp,
              eval_mate: message.eval_mate ?? null,
            };
            return setNodeAnalysis(setNodeMove(current, nodeId, row), nodeId, {
              ...node.analysis,
              status: 'analyzing',
              classification: message.classification,
              evalCp: message.eval_cp,
              evalMate: message.eval_mate ?? null,
              suggestionsAfter: afterLines,
            });
          });
          return;
        }
        if (message.type === 'error') {
          throw new Error(message.detail ?? 'Could not analyze that move.');
        }
        if (message.type !== 'final') {
          return;
        }
        const data = message.response;
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
        // `best`/`second_best` are the pre-move lines (the Better-move
        // replay); `best_after`/`second_best_after` are the next mover's
        // lines, which become this node's suggestion arrow.
        const beforeLines = suggestionLinesFrom([data.best, data.second_best]);
        const afterLines = suggestionLinesFrom([
          data.best_after,
          data.second_best_after,
        ]);
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
            suggestionsBefore: beforeLines,
            suggestionsAfter: afterLines,
          });
        });
      };

      // Parse complete NDJSON lines as they arrive so each depth updates
      // the board immediately.
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      for (;;) {
        const { done, value } = await reader.read();
        if (done) {
          break;
        }
        buffer += decoder.decode(value, { stream: true });
        let newline = buffer.indexOf('\n');
        while (newline >= 0) {
          const line = buffer.slice(0, newline).trim();
          buffer = buffer.slice(newline + 1);
          if (line) {
            handleMessage(JSON.parse(line) as SandboxStreamMessage);
          }
          newline = buffer.indexOf('\n');
        }
      }
      buffer += decoder.decode();
      const tail = buffer.trim();
      if (tail) {
        handleMessage(JSON.parse(tail) as SandboxStreamMessage);
      }
    } catch (error) {
      if (controller.signal.aborted) {
        return; // A newer move owns the board; leave its node alone.
      }
      const detail =
        error instanceof Error ? error.message : 'Could not analyze that move.';
      setExploreError(detail);
      // Roll the pending node back when nothing branched from it yet; a
      // re-played existing variation keeps its last good label instead.
      if (createdPendingNode) {
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
  const activeNode = tree && activeNodeId ? tree.nodes[activeNodeId] : null;
  // Explore-mode arrow: the engine's best move for the side to move at the
  // active position (not the pre-move "better move" the batch row carries).
  const suggestionUci =
    activeNode?.analysis?.suggestionsAfter?.[0]?.moveUci ?? null;
  // No badge until the first deepening snapshot lands; the placeholder row's
  // classification must never render.
  const classificationPending =
    activeNode?.analysis?.status === 'analyzing' &&
    activeNode.analysis.classification === undefined;
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
            suggestionUci={suggestionUci}
            classificationPending={classificationPending}
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
            classificationPending={classificationPending}
          />
        }
      />
    </div>
  );
}
