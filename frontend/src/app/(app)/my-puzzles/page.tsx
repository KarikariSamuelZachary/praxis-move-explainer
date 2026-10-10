'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

import ChessBoardComponent, {
  type BoardApi,
} from '@/components/board/ChessBoard';
import { PUZZLE_CARD_CLASS } from '@/components/puzzles/puzzleStyles';
import PuzzleStatusPanel from '@/components/puzzles/PuzzleStatusPanel';
import { WOOD_PANEL_CLASS, WOOD_PANEL_STYLE } from '@/lib/woodPanel';
import type { Puzzle } from '@/types';

type ExtractJob = {
  job_id: string;
  status: string;
  lichess_username?: string | null;
  chesscom_username?: string | null;
  requested_limit: number;
  fetched_games: number;
  screened_games: number;
  candidates: number;
  puzzles_kept: number;
  error_message?: string | null;
  summary?: {
    exclusions?: Record<string, number>;
    rejects?: Record<string, number>;
  } | null;
};

type QueueEntry = {
  id: string;
  puzzle_id: string;
  due?: string | null;
  state: number;
  reps: number;
  lapses: number;
  puzzle: {
    id: string;
    fen_before: string;
    best_move_uci: string;
    best_move_san: string;
    played_move_san: string;
    game_url: string;
    move_number: number;
    color: string;
    side_to_move: string;
  };
};

type Provider = 'lichess' | 'chesscom';

async function api(path: string, init?: RequestInit) {
  const res = await fetch(path, { cache: 'no-store', ...init });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      body.detail || body.error || `Request failed (${res.status})`
    );
  }
  return res.json();
}

function CloseIcon() {
  return (
    <svg className="h-4 w-4" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" viewBox="0 0 24 24">
      <path d="M18 6 6 18M6 6l12 12" />
    </svg>
  );
}

function ExtractDialog({
  onClose,
  onDone,
}: {
  onClose: () => void;
  onDone: (status: ExtractJob) => Promise<void>;
}) {
  const [provider, setProvider] = useState<Provider>('lichess');
  const [lichess, setLichess] = useState('');
  const [chesscom, setChesscom] = useState('');
  const [limit, setLimit] = useState(20);
  const [phase, setPhase] = useState<'idle' | 'working' | 'error'>('idle');
  const [error, setError] = useState<string | null>(null);
  const [statusLine, setStatusLine] = useState('');

  const username = (provider === 'lichess' ? lichess : chesscom).trim();
  const working = phase === 'working';

  async function handleSubmit() {
    if (!username || working) return;
    setPhase('working');
    setError(null);
    setStatusLine('Starting extraction…');
    try {
      const started = await api('/api/my-puzzles/extract', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          lichess_username: provider === 'lichess' ? username : null,
          chesscom_username: provider === 'chesscom' ? username : null,
          limit,
        }),
      });
      const jobId = started.job_id as string;
      for (let attempt = 0; attempt < 600; attempt += 1) {
        await new Promise((r) => setTimeout(r, 2000));
        let status: ExtractJob;
        try {
          status = await api(
            `/api/my-puzzles/extract/${encodeURIComponent(jobId)}`
          );
        } catch (e) {
          // A just-created job can briefly 404 while routes settle; retry.
          if (/404/.test(e instanceof Error ? e.message : '') && attempt < 3) {
            continue;
          }
          throw e;
        }
        setStatusLine(
          `Fetched ${status.fetched_games}, screened ${status.screened_games}, kept ${status.puzzles_kept}…`
        );
        if (status.status === 'completed') {
          await onDone(status);
          onClose();
          return;
        }
        if (status.status === 'failed') {
          throw new Error(status.error_message || 'Extraction failed.');
        }
      }
      throw new Error('Extraction is taking longer than expected — check back shortly.');
    } catch (e) {
      setPhase('error');
      setError(e instanceof Error ? e.message : 'Extraction failed.');
    }
  }

  function handleClose() {
    if (working) return;
    onClose();
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm px-4"
      onClick={handleClose}
      role="dialog"
      aria-modal="true"
      aria-label="Extract puzzles from your games"
    >
      <div
        className={`${PUZZLE_CARD_CLASS} relative w-full max-w-md rounded-2xl p-5`}
        onClick={(event) => event.stopPropagation()}
      >
        <button
          type="button"
          onClick={handleClose}
          disabled={working}
          aria-label="Close"
          className="absolute right-3 top-3 flex h-8 w-8 items-center justify-center rounded-lg text-[#f7e5c6]/70 transition hover:bg-white/10 hover:text-[#f7e5c6] disabled:opacity-40 disabled:pointer-events-none"
        >
          <CloseIcon />
        </button>

        <h2 className="text-xl font-semibold text-[#f7e5c6]">My puzzles</h2>
        <p className="mt-1 text-sm text-[#f7e5c6]/60">
          Fetch your recent games. We&apos;ll mine the positions where you
          missed the best move into practice puzzles.
        </p>

        <div className="mt-4 grid grid-cols-2 overflow-hidden rounded-lg border border-black/50 bg-black/50 p-1">
          {(['lichess', 'chesscom'] as Provider[]).map((key) => (
            <button
              key={key}
              type="button"
              onClick={() => setProvider(key)}
              disabled={working}
              className={`inline-flex h-10 items-center justify-center rounded-md text-sm font-semibold capitalize transition disabled:opacity-60 ${
                provider === key
                  ? 'bg-[#f7e5c6] text-[#241206]'
                  : 'text-[#f7e5c6]/70 hover:bg-white/10'
              }`}
            >
              {key === 'lichess' ? 'Lichess' : 'Chess.com'}
            </button>
          ))}
        </div>

        <div className="mt-3 flex gap-2">
          <input
            type="text"
            value={provider === 'lichess' ? lichess : chesscom}
            onChange={(event) =>
              provider === 'lichess'
                ? setLichess(event.target.value)
                : setChesscom(event.target.value)
            }
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault();
                handleSubmit();
              }
            }}
            placeholder={`${provider === 'lichess' ? 'Lichess' : 'Chess.com'} username`}
            disabled={working}
            className="min-w-0 flex-1 rounded-xl border border-black/50 bg-black/60 px-3 py-2 text-sm text-white outline-none transition placeholder:text-white/30 focus:border-emerald-400/60 focus:ring-2 focus:ring-emerald-400/20 disabled:opacity-60"
          />
        </div>

        <label className="mt-3 flex items-center gap-2 text-sm text-[#f7e5c6]/70">
          Games per site
          <input
            type="number"
            min={1}
            max={50}
            value={limit}
            onChange={(event) => setLimit(Number(event.target.value) || 20)}
            disabled={working}
            className="w-20 rounded-xl border border-black/50 bg-black/60 px-3 py-2 text-sm text-white outline-none disabled:opacity-60"
          />
        </label>

        <button
          type="button"
          onClick={handleSubmit}
          disabled={!username || working}
          className="mt-4 inline-flex h-10 w-full items-center justify-center gap-2 rounded-xl bg-emerald-500 font-semibold text-emerald-950 transition hover:bg-emerald-400 disabled:pointer-events-none disabled:opacity-40"
        >
          {working ? (
            <span className="h-4 w-4 rounded-full border-2 border-emerald-950/40 border-t-emerald-950 animate-spin" />
          ) : null}
          {working ? 'Extracting…' : 'Get puzzles'}
        </button>

        {working && (
          <p className="mt-2 text-sm text-[#f7e5c6]/60">{statusLine}</p>
        )}
        {error && <p className="mt-2 text-sm text-red-400">{error}</p>}
      </div>
    </div>
  );
}

function getSideToMoveLabel(fen: string): 'White' | 'Black' {
  return fen.split(/\s+/)[1] === 'b' ? 'Black' : 'White';
}

export default function MyPuzzlesPage() {
  const [queue, setQueue] = useState<QueueEntry[] | null>(null);
  const [queueError, setQueueError] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  // Last completed extraction, so a 0-kept run can explain itself.
  const [lastRun, setLastRun] = useState<ExtractJob | null>(null);
  const [index, setIndex] = useState(0);
  const [completed, setCompleted] = useState(0);
  const [feedback, setFeedback] = useState<'idle' | 'correct' | 'mistake'>(
    'idle'
  );
  const [verdict, setVerdict] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const boardApi = useRef<BoardApi | null>(null);
  const scoredRef = useRef(false);
  const hintsUsedRef = useRef(0);
  const startTimeRef = useRef(Date.now());
  const advanceTimerRef = useRef<number | null>(null);

  const current = queue && index < queue.length ? queue[index] : null;

  const loadQueue = useCallback(async () => {
    try {
      const entries: QueueEntry[] = await api('/api/my-puzzles/queue');
      setQueue(entries);
      setIndex(0);
      setCompleted(0);
      setFeedback('idle');
      setVerdict(null);
      scoredRef.current = false;
      hintsUsedRef.current = 0;
      startTimeRef.current = Date.now();
      if (entries.length === 0) setDialogOpen(true);
    } catch (e) {
      setQueueError(e instanceof Error ? e.message : 'Failed to load queue');
    }
  }, []);

  useEffect(() => {
    loadQueue();
  }, [loadQueue]);

  useEffect(
    () => () => {
      if (advanceTimerRef.current !== null) {
        window.clearTimeout(advanceTimerRef.current);
      }
    },
    []
  );

  const advance = useCallback(() => {
    if (!queue) return;
    if (advanceTimerRef.current !== null) {
      window.clearTimeout(advanceTimerRef.current);
      advanceTimerRef.current = null;
    }
    setFeedback('idle');
    setVerdict(null);
    scoredRef.current = false;
    hintsUsedRef.current = 0;
    startTimeRef.current = Date.now();
    if (index + 1 >= queue.length) {
      setCompleted(queue.length);
      setIndex(queue.length);
      return;
    }
    setCompleted((c) => c + 1);
    setIndex((i) => i + 1);
    boardApi.current?.resetPuzzle();
  }, [index, queue]);

  // Server-graded attempt: the board reports the attempted UCI, the backend
  // decides solved/wrong. Only the FIRST attempt per card is graded (the
  // once-per-card guard); retries are free practice.
  const handleMoveAttempted = useCallback(
    async (uci: string) => {
      if (!current || scoredRef.current || busy) return;
      scoredRef.current = true;
      setBusy(true);
      try {
        const res = await api('/api/my-puzzles/attempts', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            entry_id: current.id,
            move_uci: uci,
            time_taken_ms: Date.now() - startTimeRef.current,
            hints_used: hintsUsedRef.current,
          }),
        });
        if (res.solved) {
          setFeedback('correct');
          setVerdict(null);
          advanceTimerRef.current = window.setTimeout(advance, 1500);
        } else {
          setFeedback('mistake');
          setVerdict(
            `Not quite — the move was ${res.best_move_san}. Try again, or skip.`
          );
        }
      } catch (e) {
        scoredRef.current = false;
        setVerdict(e instanceof Error ? e.message : 'Attempt failed');
      } finally {
        setBusy(false);
      }
    },
    [advance, busy, current]
  );

  const handleSkip = useCallback(() => {
    scoredRef.current = true;
    advance();
  }, [advance]);

  const handleBadPuzzle = useCallback(async () => {
    if (!current || busy) return;
    setBusy(true);
    try {
      await api('/api/my-puzzles/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ entry_id: current.id }),
      });
      advance();
      await loadQueue().catch(() => undefined);
    } catch (e) {
      setVerdict(e instanceof Error ? e.message : 'Feedback failed');
    } finally {
      setBusy(false);
    }
  }, [advance, busy, current, loadQueue]);

  const boardPuzzle: Puzzle | null = current
    ? {
        id: current.puzzle.id,
        fen: current.puzzle.fen_before,
        moves: [current.puzzle.best_move_uci],
        rating: 1500,
        themes: ['myGame'],
        gameUrl: current.puzzle.game_url,
      }
    : null;

  const total = queue?.length ?? 0;
  const result =
    feedback === 'correct'
      ? 'solved'
      : feedback === 'mistake'
        ? 'failed'
        : null;

  return (
    <div className="min-h-[calc(100vh-2.5rem)] -mt-2 text-white [background-image:url(/walnut-dark.webp)] [background-size:cover] [background-position:center] xl:h-[calc(100vh-2.5rem)] xl:overflow-hidden">
      <div className="mx-auto flex flex-col px-6 pb-1 lg:px-10 xl:h-full">
        {dialogOpen && (
          <ExtractDialog
            onClose={() => setDialogOpen(false)}
            onDone={async (status) => {
              setLastRun(status);
              await loadQueue();
            }}
          />
        )}

        {queue === null && !queueError ? (
          <div className="flex h-[70vh] items-center justify-center">
            <div className={`${PUZZLE_CARD_CLASS} px-10 py-8 text-center shadow-2xl shadow-black/30`}>
              <div className="mx-auto mb-4 h-9 w-9 animate-spin rounded-full border-2 border-[#10b981] border-t-transparent" />
              <p className="text-white/60">Loading your puzzles...</p>
            </div>
          </div>
        ) : queueError && (queue === null || queue.length === 0) ? (
          <div className="flex h-[70vh] items-center justify-center">
            <div className={`${PUZZLE_CARD_CLASS} max-w-md px-8 py-6 text-center`}>
              <p className="text-white/70">{queueError}</p>
              <button
                type="button"
                onClick={() => setDialogOpen(true)}
                className="mt-4 w-full rounded-lg border border-white/20 bg-white/5 px-4 py-2 text-sm font-semibold text-white transition hover:bg-white/10"
              >
                Extract puzzles
              </button>
            </div>
          </div>
        ) : (
        <div className="grid gap-6 xl:min-h-0 xl:flex-1 xl:grid-cols-[20rem_minmax(0,1fr)_22rem] xl:pt-5">
          {/* ============ LEFT: QUEUE INFO ============ */}
          <section className="order-2 mt-6 flex min-h-0 flex-col gap-5 xl:order-none xl:mt-0 xl:h-[min(calc(100vw-800px),calc(100vh-70px))]">
            <div
              className={`${WOOD_PANEL_CLASS} flex shrink-0 items-center gap-4 p-5`}
              style={WOOD_PANEL_STYLE}
            >
              <div className="min-w-0 flex-1">
                <div className="text-[11px] font-bold uppercase tracking-[0.25em] text-white/40">
                  My Puzzles due
                </div>
                <div className="mt-1.5 flex items-end gap-2.5">
                  <span className="font-display text-[34px] font-semibold leading-none text-[#f7e5c6]">
                    {total - completed}
                  </span>
                  <span className="mb-0.5 rounded-full bg-white/10 px-2 py-0.5 text-xs font-bold text-white/60">
                    {total === 0
                      ? 'queue empty'
                      : `${completed} of ${total} done`}
                  </span>
                </div>
                <p className="mt-2 text-[11px] leading-4 text-white/40">
                  Mined from your own games — find the move you missed.
                </p>
              </div>
            </div>

            <div className={`${PUZZLE_CARD_CLASS} shrink-0 p-5`}>
              <div className="text-[11px] font-bold uppercase tracking-[0.25em] text-white/40">
                This card
              </div>
              {current ? (
                <div className="mt-2 text-sm leading-6 text-[#f7e5c6]/85">
                  <p>
                    Card {index + 1} of {total} — from{' '}
                    <a
                      href={current.puzzle.game_url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-emerald-300 underline"
                    >
                      your game
                    </a>
                    , move {current.puzzle.move_number}.
                  </p>
                  <p>
                    You played{' '}
                    <span className="font-semibold text-[#f7e5c6]">
                      {current.puzzle.played_move_san}
                    </span>
                    .
                  </p>
                  {verdict && feedback === 'mistake' && (
                    <p className="mt-1 text-amber-200">{verdict}</p>
                  )}
                  {feedback === 'correct' && (
                    <p className="mt-1 text-emerald-300">
                      Correct — the move you missed.
                    </p>
                  )}
                </div>
              ) : (
                <p className="mt-2 text-sm text-white/50">
                  {lastRun && lastRun.status === 'completed' && lastRun.puzzles_kept === 0 ? (
                    <>
                      No keepers this time — screened {lastRun.screened_games}{' '}
                      games, found {lastRun.candidates} candidate mistakes,
                      but none had a single clear best move
                      {lastRun.summary?.rejects
                        ? ` (${Object.entries(lastRun.summary.rejects)
                            .map(([k, v]) => `${v} ${k}`)
                            .join(', ')})`
                        : ''}
                      {lastRun.summary?.exclusions
                        ? `; excluded ${Object.entries(
                            lastRun.summary.exclusions
                          )
                            .map(([k, v]) => `${v} ${k}`)
                            .join(', ')}`
                        : ''}
                      . Try more games or longer time controls.
                    </>
                  ) : (
                    'Queue clear. Extract more games to keep practicing.'
                  )}
                </p>
              )}
              <button
                type="button"
                onClick={() => setDialogOpen(true)}
                className="mt-4 w-full rounded-lg border border-white/20 bg-white/5 px-4 py-2 text-sm font-semibold text-white transition hover:bg-white/10"
              >
                Extract more
              </button>
            </div>
          </section>

          {/* ============ CENTER: CHESSBOARD (same size as puzzles page) ============ */}
          <section className="order-1 min-h-0 min-w-0 xl:order-none xl:mt-0">
            <div className="relative mx-auto mt-6 aspect-square w-full max-w-[calc(100vh-70px)] xl:mt-0">
              {current && boardPuzzle && (
                <div className="w-full">
                  <ChessBoardComponent
                    key={current.id}
                    puzzle={boardPuzzle}
                    onPuzzleSolved={() => undefined}
                    onPuzzleFailed={() => undefined}
                    onMoveAttempted={handleMoveAttempted}
                    onHintRevealed={() => {
                      hintsUsedRef.current += 1;
                    }}
                    onSolutionRevealed={() => {
                      hintsUsedRef.current += 1;
                    }}
                    apiRef={boardApi}
                  />
                </div>
              )}
            </div>
          </section>

          {/* ============ RIGHT: STATUS + ACTIONS ============ */}
          <section className="order-3 mx-auto flex w-full max-w-[420px] flex-col gap-5 xl:order-none xl:mx-0 xl:mt-0 xl:max-w-none xl:min-h-0">
            <div className="wooden-scroll xl:min-h-0 xl:overflow-y-auto">
              {current && (
                <PuzzleStatusPanel
                  sideToMoveLabel={getSideToMoveLabel(
                    current.puzzle.fen_before
                  )}
                  themeLabel={null}
                  result={result}
                  onHint={() => boardApi.current?.showHint()}
                  onShowSolution={() => boardApi.current?.showSolution()}
                />
              )}
            </div>

            {result && (
              <button
                type="button"
                onClick={advance}
                className={`${PUZZLE_CARD_CLASS} flex h-14 w-full shrink-0 items-center justify-center gap-3 text-sm font-semibold text-white transition hover:bg-white/5`}
              >
                Next Puzzle
              </button>
            )}
            {!result && (
              <button
                type="button"
                onClick={handleSkip}
                className={`${PUZZLE_CARD_CLASS} flex h-14 w-full shrink-0 items-center justify-center gap-3 text-sm font-semibold text-white transition hover:bg-white/5`}
              >
                Skip
              </button>
            )}
            <button
              type="button"
              onClick={handleBadPuzzle}
              className="flex w-full shrink-0 items-center justify-center gap-3 rounded-xl border border-red-300/20 bg-red-400/10 px-4 py-3 text-sm font-semibold text-red-200 transition hover:bg-red-400/20"
            >
              Bad puzzle
            </button>
          </section>
        </div>
        )}
      </div>
    </div>
  );
}
