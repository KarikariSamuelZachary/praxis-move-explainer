'use client';

import { useEffect, useRef, useState } from 'react';
import dynamic from 'next/dynamic';
import { Chess } from 'chess.js';

import AnalysisPanel from '@/components/review/AnalysisPanel';
import GameReviewSummary, {
  type ReviewSummaryProgress,
} from '@/components/review/GameReviewSummary';
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

import {
  type ReviewExplanation,
  displayedExplanationFor,
  lastPlyFor,
} from './review-page-logic';
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

type AnalyzeErrorResponse = {
  detail?: string;
  error?: string;
};

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

/** First usable engine move for the active position's board arrow.
 *  This is the best move for the side to move at the position (the next
 *  position's best), never the pre-move "better move" of the last played move. */
function suggestionFrom(
  lines: Array<SandboxLine | null | undefined>,
): { uci: string; san: string | null } | null {
  const line = lines.find((candidate) => candidate?.move_uci);
  if (!line?.move_uci) {
    return null;
  }
  return { uci: line.move_uci, san: line.move_san ?? null };
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
    useState<ReviewSummaryProgress | null>(null);
  // Right panel view once a game exists: chess.com-style overview first,
  // move-by-move analysis after "Start Review".
  const [rightView, setRightView] = useState<'summary' | 'moves'>('summary');
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
  // position (the side to move) and draw it as an arrow. Debounced ~150ms
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
      node.analysis?.suggestionUci
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
          const suggestion = suggestionFrom([
            data.best,
            data.second_best,
          ]);
          if (!suggestion) {
            return;
          }
          setTree((current) => {
            const currentNode = current?.nodes[activeNodeId];
            if (!current || !currentNode) {
              return current;
            }
            return setNodeAnalysis(current, activeNodeId, {
              ...currentNode.analysis,
              suggestionUci: suggestion.uci,
              suggestionSan: suggestion.san,
            });
          });
        })
        .catch(() => {
          // Prewarming is best-effort; explores work without it.
        });
    }, 150);
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
          // best_after is the next mover's best (for the new position), not
          // the pre-move "should have played" line.
          const suggestion = suggestionFrom([message.best_after]);
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
              classificationReady: true,
              suggestionUci: suggestion?.uci ?? node.analysis?.suggestionUci ?? null,
              suggestionSan: suggestion?.san ?? node.analysis?.suggestionSan ?? null,
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
        const suggestion = suggestionFrom([
          data.best_after,
          data.second_best_after,
        ]);
        setTree((current) => {
          if (!current) {
            return current;
          }
          const withMove = setNodeMove(current, nodeId, row);
          return setNodeAnalysis(withMove, nodeId, {
            suggestionUci: suggestion?.uci ?? null,
            suggestionSan: suggestion?.san ?? null,
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
  const activePly = view?.activePly ?? 0;
  const activeNode =
    view && tree && activeNodeId ? tree.nodes[activeNodeId] : null;
  // Explore-mode arrow: the engine's best move for the side to move at the
  // active position (not the pre-move "better move" the batch row carries).
  const suggestionUci = activeNode?.analysis?.suggestionUci ?? null;
  const suggestionSan = activeNode?.analysis?.suggestionSan ?? null;
  // No badge until the first deepening snapshot lands; the placeholder row's
  // classification must never render.
  const classificationPending =
    activeNode?.analysis?.status === 'analyzing' &&
    !activeNode.analysis.classificationReady;
  const displayedExplanation = displayedExplanationFor(
    currentMove,
    coachExplanation,
  );
  const moveNumberLabel = view?.moveNumberLabel ?? 'Starting position';
  // In explore mode the "best move" is the suggestion for the side to move
  // at the current position (the next position's best). Outside explore it
  // is the batch row's pre-move line (what should have been played instead).
  const reviewBestMoveSan = view?.bestMoveSan ?? null;
  const bestMoveSan = exploreMode ? (suggestionSan ?? null) : reviewBestMoveSan;

  function handlePlySelect(ply: number) {
    if (!tree) {
      return;
    }
    const nodeId = mainlinePlyToNode(tree, ply);
    if (nodeId) {
      setActiveNodeId(nodeId);
    }
  }

  function handleStartReview() {
    if (!tree) {
      return;
    }
    // Jump to the first played move (ply 1 when the Start row exists) and
    // flip the right panel to the move-by-move view.
    const firstPly = tree.mainlineIds.length > 1 ? 1 : 0;
    handlePlySelect(firstPly);
    setRightView('moves');
  }

  // A fresh import/analysis always lands back on the overview.
  useEffect(() => {
    if (analysisState === 'analyzing' || analysisState === 'ready') {
      setRightView('summary');
    }
  }, [analysisState]);

  // Where the reviewed game came from. Only chess.com has profile
  // pictures (lichess has none, pasted PGNs have no known provider).
  const reviewPlatform = importSource === 'chesscom' ? 'chesscom' : null;
  // The import form stays on the left. The right panel shows a placeholder
  // before analysis, a progress summary while analyzing, and the overview
  // after analysis completes.
  const isAnalyzingNow = analysisState === 'analyzing';

  const movesPanel = (
    <AnalysisPanel
      currentMove={currentMove}
      hasGame={hasGame}
      moves={gameData}
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
  );

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
            isAnalyzing={isAnalyzingNow}
            errorMessage={analysisState === 'error' ? errorMessage : null}
            disabled={!hasGame && isAnalyzingNow}
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
            onToggleExplore={() => {
              setShowBestMove(false);
              setExploreMode((value) => !value);
            }}
            sandboxEnabled={sandboxEnabled}
            suggestionUci={suggestionUci}
            classificationPending={classificationPending}
          />
        }
        analysisPanel={
          isAnalyzingNow ? (
            <GameReviewSummary
              pgn={pgnInput}
              moves={null}
              isAnalyzing
              progress={analysisProgress}
              onStartReview={handleStartReview}
              platform={reviewPlatform}
            />
          ) : hasGame ? (
            <div className="flex h-full min-h-0 min-w-0 flex-col gap-2 overflow-hidden">
              <div
                role="tablist"
                aria-label="Right panel view"
                className="flex shrink-0 gap-1 rounded-xl border border-black/40 bg-black/40 p-1"
              >
                {(
                  [
                    { key: 'summary', label: 'Overview' },
                    { key: 'moves', label: 'Moves' },
                  ] as const
                ).map((tab) => {
                  const isActive = rightView === tab.key;
                  return (
                    <button
                      key={tab.key}
                      type="button"
                      role="tab"
                      aria-selected={isActive}
                      onClick={() => setRightView(tab.key)}
                      className={`flex min-w-0 flex-1 cursor-pointer items-center justify-center rounded-lg px-2 py-1.5 text-xs font-medium transition ${
                        isActive
                          ? 'bg-[#f7e5c6]/15 text-[#f7e5c6] ring-1 ring-[#f7e5c6]/40'
                          : 'text-[#f7e5c6]/70 hover:text-[#f7e5c6]'
                      }`}
                    >
                      <span className="truncate">{tab.label}</span>
                    </button>
                  );
                })}
              </div>
              <div className="min-h-0 min-w-0 flex-1 overflow-hidden">
                {rightView === 'summary' ? (
                  <GameReviewSummary
                    pgn={pgnInput}
                    moves={gameData}
                    isAnalyzing={false}
                    progress={null}
                    onStartReview={handleStartReview}
                    platform={reviewPlatform}
                  />
                ) : (
                  movesPanel
                )}
              </div>
            </div>
          ) : (
            movesPanel
          )
        }
      />
    </div>
  );
}
