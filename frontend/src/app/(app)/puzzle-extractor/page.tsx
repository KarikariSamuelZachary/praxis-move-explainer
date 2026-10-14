'use client';

import dynamic from 'next/dynamic';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import ChessBoardComponent, {
  type BoardApi,
} from '@/components/board/ChessBoard';
import { ProviderIcon } from '@/components/icons/PlatformIcons';
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
  theme?: string | null;
  puzzle: {
    id: string;
    fen_before: string;
    best_move_uci: string;
    best_move_san: string;
    solution_moves_uci: string[];
    played_move_san: string;
    game_url: string;
    move_number: number;
    color: string;
    side_to_move: string;
    source_username: string;
    source_provider: string;
    opponent: string;
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

const STARTING_FEN =
  'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';

const Chessboard = dynamic(
  () => import('react-chessboard').then((module) => module.Chessboard),
  {
    ssr: false,
    loading: () => (
      <div
        className="h-full w-full animate-pulse rounded-md bg-black/35"
        aria-hidden="true"
      />
    ),
  }
);

function BoardThumb() {
  return (
    <div className="h-28 w-28 shrink-0 overflow-hidden rounded-lg shadow-lg shadow-black/50 ring-1 ring-black/60">
      <Chessboard
        options={{
          position: STARTING_FEN,
          allowDragging: false,
          showNotation: false,
          darkSquareStyle: {
            backgroundImage: 'url(/walnut-dark.webp)',
            backgroundSize: '140% 140%',
            backgroundPosition: 'center',
          },
          lightSquareStyle: {
            backgroundImage: 'url(/walnut-light.webp)',
            backgroundSize: '140% 140%',
            backgroundPosition: 'center',
          },
          boardStyle: { width: '100%', height: '100%' },
        }}
      />
    </div>
  );
}

const DECK_CARD_CLASS =
  'rounded-2xl border border-[#3a2410]/70 bg-[#1b120d]/55 backdrop-blur-sm [box-shadow:0_8px_24px_rgba(0,0,0,0.45),inset_0_1px_0_rgba(255,200,100,0.08),inset_0_-1px_0_rgba(0,0,0,0.5)]';

type Deck = {
  key: string;
  username: string;
  provider: string;
  sourceUsername: string;
};

function deckOf(entry: QueueEntry): Deck {
  const sourceUsername = entry.puzzle.source_username || '';
  const provider = entry.puzzle.source_provider || '';
  const username = sourceUsername || 'Imported games';
  return { key: `${provider} ${username}`, username, provider, sourceUsername };
}

function providerLabel(provider: string): string {
  if (provider === 'lichess') return 'Lichess';
  if (provider === 'chesscom') return 'Chess.com';
  return '';
}

function ExternalLinkIcon() {
  return (
    <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
      <path d="M15 3h6v6" />
      <path d="M10 14 21 3" />
    </svg>
  );
}

function BackIcon() {
  return (
    <svg className="h-4 w-4" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" viewBox="0 0 24 24" aria-hidden="true">
      <path d="m15 18-6-6 6-6" />
    </svg>
  );
}

function SettingsIcon() {
  return (
    <svg className="h-4 w-4" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" viewBox="0 0 24 24">
      <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0L6.2 6.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.09a2 2 0 0 1 1 1.74v.5a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.38a2 2 0 0 0-.73-2.73l-.15-.09a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2Z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg
      className="h-5 w-5"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M3 6h18" />
      <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
      <path d="M19 6 18 20a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
      <path d="M10 11v6" />
      <path d="M14 11v6" />
    </svg>
  );
}

function DeckCard({
  deck,
  due,
  onOpen,
  onDelete,
}: {
  deck: Deck;
  due: number;
  onOpen: () => void;
  onDelete: () => void;
}) {
  const site = providerLabel(deck.provider);
  return (
    <article className={`${DECK_CARD_CLASS} relative flex items-stretch gap-4 p-4 transition hover:bg-[#241610]/60`}>
      <button
        type="button"
        onClick={onOpen}
        aria-label={`Solve ${deck.username}`}
        className="absolute inset-0 z-0 rounded-2xl focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#d9b87c]"
      />
      <div className="relative z-10 pointer-events-none shrink-0">
        <BoardThumb />
      </div>
      <div className="relative z-10 pointer-events-none flex min-w-0 flex-1 flex-col justify-center py-1 pr-10">
        <h3 className="truncate font-display text-xl font-semibold text-[#f7e5c6]">
          {deck.username}
        </h3>
        <div className="mt-2 flex items-center gap-2">
          {site && (
            <span className="rounded-full bg-white/10 px-2 py-0.5 text-xs font-bold text-white/60">
              {site}
            </span>
          )}
          <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-xs font-bold text-emerald-300">
            {due} due
          </span>
        </div>
      </div>
      <button
        type="button"
        onClick={(event) => {
          event.stopPropagation();
          onDelete();
        }}
        aria-label={`Remove ${deck.username}`}
        title="Remove deck"
        className="absolute right-3 top-1/2 z-20 flex h-9 w-9 -translate-y-1/2 items-center justify-center rounded-lg text-[#d97757] transition hover:bg-[#d97757]/10 hover:text-[#f5b39a] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#d97757]"
      >
        <TrashIcon />
      </button>
    </article>
  );
}

function ConfirmDeleteDeckDialog({
  deck,
  onCancel,
  onConfirm,
  pending,
}: {
  deck: Deck;
  onCancel: () => void;
  onConfirm: () => void;
  pending: boolean;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm px-4"
      onClick={pending ? undefined : onCancel}
      role="dialog"
      aria-modal="true"
      aria-label={`Remove ${deck.username}`}
    >
      <div
        className={`${PUZZLE_CARD_CLASS} relative w-full max-w-sm rounded-2xl p-5`}
        onClick={(event) => event.stopPropagation()}
      >
        <h2 className="font-display text-lg font-semibold text-[#f7e5c6]">
          Remove &ldquo;{deck.username}&rdquo;?
        </h2>
        <p className="mt-2 text-sm text-[#f7e5c6]/60">
          This permanently removes every puzzle in this deck.
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={pending}
            className="h-9 rounded-lg border border-black/50 bg-black/40 px-4 text-sm font-semibold text-[#f7e5c6]/70 transition hover:bg-black/60 hover:text-[#f7e5c6] disabled:opacity-40 disabled:pointer-events-none"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={pending}
            className="h-9 rounded-lg bg-[#d97757] px-4 text-sm font-semibold text-white shadow-lg shadow-orange-950/40 transition hover:bg-[#c6654a] disabled:pointer-events-none disabled:opacity-40"
          >
            {pending ? 'Removing…' : 'Remove'}
          </button>
        </div>
      </div>
    </div>
  );
}

function ExtractDialog({
  onClose,
  onDone,
  onStarted,
}: {
  onClose: () => void;
  onDone: (status: ExtractJob) => Promise<void>;
  onStarted: (jobId: string, limit: number) => void;
}) {
  const [provider, setProvider] = useState<Provider>('lichess');
  const [lichess, setLichess] = useState('');
  const [chesscom, setChesscom] = useState('');
  const [limitText, setLimitText] = useState('20');
  const [phase, setPhase] = useState<'idle' | 'working' | 'error'>('idle');
  const [error, setError] = useState<string | null>(null);
  const [statusLine, setStatusLine] = useState('');
  const cancelledRef = useRef(false);

  useEffect(
    () => () => {
      cancelledRef.current = true;
    },
    []
  );

  const username = (provider === 'lichess' ? lichess : chesscom).trim();
  const working = phase === 'working';

  function parsedLimit(): number {
    const n = parseInt(limitText, 10);
    if (!Number.isFinite(n)) return 20;
    return Math.min(50, Math.max(1, n));
  }

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
          limit: parsedLimit(),
        }),
      });
      const jobId = started.job_id as string;
      onStarted(jobId, parsedLimit());
      for (let attempt = 0; attempt < 600; attempt += 1) {
        if (cancelledRef.current) return;
        await new Promise((r) => setTimeout(r, 2000));
        if (cancelledRef.current) return;
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
        if (cancelledRef.current) return;
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
      if (cancelledRef.current) return;
      throw new Error('Extraction is taking a while. Check back shortly.');
    } catch (e) {
      if (cancelledRef.current) return;
      setPhase('error');
      setError(e instanceof Error ? e.message : 'Extraction failed.');
    }
  }

  function handleClose() {
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
          aria-label="Close"
          className="absolute right-3 top-3 flex h-8 w-8 items-center justify-center rounded-lg text-[#f7e5c6]/70 transition hover:bg-white/10 hover:text-[#f7e5c6]"
        >
          <CloseIcon />
        </button>

        <h2 className="text-xl font-semibold text-[#f7e5c6]">Puzzle Extractor</h2>
        <p className="mt-1 text-sm text-[#f7e5c6]/60">
          Fetch your recent games. We&apos;ll mine the positions where you
          missed the best move into practice puzzles. You can close this
          and come back later.
        </p>

        <div className="mt-4 grid grid-cols-2 overflow-hidden rounded-lg border border-black/50 bg-black/50 p-1">
          {(['lichess', 'chesscom'] as Provider[]).map((key) => (
            <button
              key={key}
              type="button"
              onClick={() => setProvider(key)}
              disabled={working}
              className={`inline-flex h-10 items-center justify-center gap-2 rounded-md text-sm font-semibold capitalize transition disabled:opacity-60 ${
                provider === key
                  ? 'bg-[#f7e5c6] text-[#241206]'
                  : 'text-[#f7e5c6]/70 hover:bg-white/10'
              }`}
            >
              <ProviderIcon provider={key} />
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
            value={limitText}
            onChange={(event) => setLimitText(event.target.value)}
            onBlur={() => setLimitText(String(parsedLimit()))}
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

export default function PuzzleExtractorPage() {
  const [queue, setQueue] = useState<QueueEntry[] | null>(null);
  const [queueError, setQueueError] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [deckKey, setDeckKey] = useState<string | null>(null);
  const [clearError, setClearError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<Deck | null>(null);
  const [deletePending, setDeletePending] = useState(false);
  const [index, setIndex] = useState(0);
  const [completed, setCompleted] = useState(0);
  const [feedback, setFeedback] = useState<'idle' | 'correct' | 'mistake'>(
    'idle'
  );
  const [busy, setBusy] = useState(false);
  const [autoAdvance, setAutoAdvance] = useState(true);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [activeJob, setActiveJob] = useState<ExtractJob | null>(null);

  const boardApi = useRef<BoardApi | null>(null);
  const settingsRef = useRef<HTMLDivElement>(null);
  const scoredRef = useRef(false);
  const attemptedMovesRef = useRef<string[]>([]);
  const hintsUsedRef = useRef(0);
  const startTimeRef = useRef(Date.now());
  const advanceTimerRef = useRef<number | null>(null);

  const decks = useMemo(() => {
    const map = new Map<string, { deck: Deck; due: number }>();
    for (const entry of queue ?? []) {
      const deck = deckOf(entry);
      const found = map.get(deck.key);
      if (found) found.due += 1;
      else map.set(deck.key, { deck, due: 1 });
    }
    return [...map.values()].sort((a, b) => b.due - a.due);
  }, [queue]);

  const visible = useMemo(() => {
    if (!queue) return null;
    if (!deckKey) return queue;
    return queue.filter((entry) => deckOf(entry).key === deckKey);
  }, [queue, deckKey]);

  const activeDeck =
    decks.find((d) => d.deck.key === deckKey)?.deck ?? null;

  const openDeck = useCallback((key: string) => {
    setDeckKey(key);
    setIndex(0);
    setCompleted(0);
    setFeedback('idle');
    scoredRef.current = false;
    attemptedMovesRef.current = [];
    hintsUsedRef.current = 0;
    startTimeRef.current = Date.now();
    boardApi.current?.resetPuzzle();
  }, []);

  const current = visible && index < visible.length ? visible[index] : null;

  const loadQueue = useCallback(async () => {
    try {
      const entries: QueueEntry[] = await api('/api/my-puzzles/queue');
      setQueue(entries);
      setDeckKey((prev) =>
        prev && entries.some((e) => deckOf(e).key === prev) ? prev : null
      );
      setIndex(0);
      setCompleted(0);
      setFeedback('idle');
      scoredRef.current = false;
      attemptedMovesRef.current = [];
      hintsUsedRef.current = 0;
      startTimeRef.current = Date.now();
      if (entries.length === 0) setDialogOpen(true);
    } catch (e) {
      setQueueError(e instanceof Error ? e.message : 'Failed to load queue');
    }
  }, []);

  useEffect(() => {
    loadQueue();
    (async () => {
      try {
        const jobs: ExtractJob[] = await api('/api/my-puzzles/jobs/recent?limit=5');
        const live = jobs.find(
          (job) => job.status === 'queued' || job.status === 'running'
        );
        if (live) setActiveJob(live);
      } catch {
        // Queue still loads; background progress is a bonus.
      }
    })();
  }, [loadQueue]);

  const activeJobId = activeJob?.job_id ?? null;
  useEffect(() => {
    if (!activeJobId) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const status: ExtractJob = await api(
          `/api/my-puzzles/extract/${encodeURIComponent(activeJobId)}`
        );
        if (cancelled) return;
        if (status.status === 'completed' || status.status === 'failed') {
          setActiveJob(null);
          await loadQueue();
        } else {
          setActiveJob(status);
        }
      } catch {
        // Keep polling; a transient fetch error is not a job failure.
      }
    };
    const id = window.setInterval(tick, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [activeJobId, loadQueue]);

  useEffect(() => {
    if (!isSettingsOpen) return;

    const handlePointerDown = (event: PointerEvent) => {
      if (settingsRef.current && !settingsRef.current.contains(event.target as Node)) {
        setIsSettingsOpen(false);
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setIsSettingsOpen(false);
    };

    document.addEventListener('pointerdown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [isSettingsOpen]);

  useEffect(() => {
    if (!autoAdvance && advanceTimerRef.current !== null) {
      window.clearTimeout(advanceTimerRef.current);
      advanceTimerRef.current = null;
    }
  }, [autoAdvance]);

  useEffect(
    () => () => {
      if (advanceTimerRef.current !== null) {
        window.clearTimeout(advanceTimerRef.current);
      }
    },
    []
  );

  const advance = useCallback(() => {
    if (!visible) return;
    if (advanceTimerRef.current !== null) {
      window.clearTimeout(advanceTimerRef.current);
      advanceTimerRef.current = null;
    }
    setFeedback('idle');
    scoredRef.current = false;
    attemptedMovesRef.current = [];
    hintsUsedRef.current = 0;
    startTimeRef.current = Date.now();
    if (index + 1 >= visible.length) {
      setDeckKey(null);
      setIndex(0);
      setCompleted(0);
      return;
    }
    setCompleted((c) => c + 1);
    setIndex((i) => i + 1);
    boardApi.current?.resetPuzzle();
  }, [index, visible]);

  // A card is graded once, after the whole line is solved or a move is missed.
  // Later retries are free practice, just as they were for single-move cards.
  const scoreAttempt = useCallback(
    async (moves: string[]) => {
      if (!current || scoredRef.current || busy) return;
      scoredRef.current = true;
      setBusy(true);
      try {
        const res = await api('/api/my-puzzles/attempts', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            entry_id: current.id,
            moves_uci: moves,
            time_taken_ms: Date.now() - startTimeRef.current,
            hints_used: hintsUsedRef.current,
          }),
        });
        if (res.solved) {
          setFeedback('correct');
          if (autoAdvance) {
            advanceTimerRef.current = window.setTimeout(advance, 1500);
          }
        } else {
          setFeedback('mistake');
        }
      } catch {
        scoredRef.current = false;
      } finally {
        setBusy(false);
      }
    },
    [advance, autoAdvance, busy, current]
  );

  const handleMoveAttempted = useCallback((uci: string, isCorrect: boolean) => {
    if (scoredRef.current) return;
    const moves = [...attemptedMovesRef.current, uci];
    attemptedMovesRef.current = moves;
    if (!isCorrect) void scoreAttempt(moves);
  }, [scoreAttempt]);

  const handlePuzzleEnd = useCallback(() => {
    void scoreAttempt(attemptedMovesRef.current);
  }, [scoreAttempt]);

  const handleDeleteDeckConfirm = useCallback(async () => {
    if (!deleting || deletePending) return;
    setDeletePending(true);
    try {
      const params = new URLSearchParams({
        source_provider: deleting.provider,
        source_username: deleting.sourceUsername,
      });
      await api(`/api/my-puzzles/puzzles?${params.toString()}`, {
        method: 'DELETE',
      });
      setDeleting(null);
      setDeckKey(null);
      await loadQueue();
    } catch (e) {
      setClearError(e instanceof Error ? e.message : 'Delete failed.');
    } finally {
      setDeletePending(false);
    }
  }, [deleting, deletePending, loadQueue]);

  // Memoized on the active entry: rebuilding this object every render
  // would retrigger the board's setup effect and snap back solution moves.
  const boardPuzzle: Puzzle | null = useMemo(
    () =>
      current
        ? {
            id: current.puzzle.id,
            fen: current.puzzle.fen_before,
            moves:
              current.puzzle.solution_moves_uci.length
                ? current.puzzle.solution_moves_uci
                : [current.puzzle.best_move_uci],
            rating: 1500,
            themes: [current.theme ?? 'myGame'],
            gameUrl: current.puzzle.game_url,
          }
        : null,
    [current]
  );
  const themeLabel = current?.theme ?? null;

  const total = visible?.length ?? 0;
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
            onStarted={(jobId, limit) => {
              setActiveJob({
                job_id: jobId,
                status: 'queued',
                requested_limit: limit,
                fetched_games: 0,
                screened_games: 0,
                candidates: 0,
                puzzles_kept: 0,
              });
            }}
            onDone={async () => {
              setActiveJob(null);
              await loadQueue();
            }}
          />
        )}
        {activeJob && !dialogOpen && (
          <div className="mx-auto mt-4 flex w-full max-w-3xl items-center gap-3 rounded-xl border border-white/10 bg-black/50 px-4 py-2.5 text-sm text-white/70">
            {activeJob.status === 'failed' ? (
              <>
                <span className="min-w-0 flex-1 truncate">
                  {activeJob.error_message || 'Extraction failed.'}
                </span>
                <button
                  type="button"
                  onClick={() => setActiveJob(null)}
                  aria-label="Dismiss"
                  className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-white/60 transition hover:bg-white/10 hover:text-white"
                >
                  <CloseIcon />
                </button>
              </>
            ) : (
              <>
                <span className="h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-[#10b981] border-t-transparent" />
                <span className="min-w-0 flex-1 truncate">
                  Extracting puzzles… {activeJob.fetched_games} fetched,{' '}
                  {activeJob.puzzles_kept} kept
                </span>
              </>
            )}
          </div>
        )}
        {deleting && (
          <ConfirmDeleteDeckDialog
            deck={deleting}
            onCancel={() => {
              if (!deletePending) setDeleting(null);
            }}
            onConfirm={handleDeleteDeckConfirm}
            pending={deletePending}
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
        ) : deckKey === null ? (
        <div className="mx-auto w-full max-w-3xl px-1 pt-6">
          <div className="flex items-center justify-between gap-4">
            <h2 className="font-display text-2xl font-semibold text-[#f7e5c6]">
              Puzzle decks
            </h2>
            <button
              type="button"
              onClick={() => setDialogOpen(true)}
              className="rounded-lg border border-white/20 bg-white/5 px-4 py-2 text-sm font-semibold text-white transition hover:bg-white/10"
            >
              Extract more
            </button>
          </div>
          {clearError && (
            <p className="mt-2 text-sm text-red-400">{clearError}</p>
          )}
          <div className="mt-4 grid gap-4 pb-8 md:grid-cols-2">
            {decks.map(({ deck, due }) => (
              <DeckCard
                key={deck.key}
                deck={deck}
                due={due}
                onOpen={() => openDeck(deck.key)}
                onDelete={() => {
                  setClearError(null);
                  setDeleting(deck);
                }}
              />
            ))}
          </div>
          {decks.length === 0 && (
            <button
              type="button"
              onClick={() => setDialogOpen(true)}
              className="mt-2 w-full rounded-lg border border-white/20 bg-white/5 px-4 py-3 text-sm font-semibold text-white transition hover:bg-white/10"
            >
              Extract puzzles
            </button>
          )}
        </div>
        ) : (
        <div className="grid gap-6 xl:min-h-0 xl:flex-1 xl:grid-cols-[20rem_minmax(0,1fr)_22rem] xl:pt-5">
          {/* ============ LEFT: QUEUE INFO ============ */}
          <section className="order-2 mt-6 flex min-h-0 flex-col gap-5 xl:order-none xl:mt-0 xl:h-[min(calc(100vw-800px),calc(100vh-70px))]">
            <button
              type="button"
              onClick={() => setDeckKey(null)}
              className="inline-flex shrink-0 items-center gap-1 self-start text-sm font-semibold text-white/60 transition hover:text-white"
            >
              <BackIcon />
              {activeDeck?.username ?? 'Decks'}
            </button>
            <div
              className={`${WOOD_PANEL_CLASS} flex shrink-0 items-center gap-4 p-5`}
              style={WOOD_PANEL_STYLE}
            >
              <div className="min-w-0 flex-1">
                <div className="text-[11px] font-bold uppercase tracking-[0.25em] text-white/40">
                  Puzzles due
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
                {current && (
                  <div className="mt-3 flex items-center gap-2 border-t border-white/10 pt-3 text-sm">
                    <span className="min-w-0 flex-1 truncate font-semibold text-[#f7e5c6]">
                      {current.puzzle.source_username || 'You'}
                    </span>
                    <span className="shrink-0 rounded bg-white/10 px-1.5 py-px text-[10px] font-bold uppercase tracking-widest text-white/40">
                      vs
                    </span>
                    <span className="min-w-0 flex-1 truncate text-right font-medium text-[#efd9a7]/90">
                      {current.puzzle.opponent || 'Opponent'}
                    </span>
                  </div>
                )}
              </div>
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
                    onPuzzleEnd={handlePuzzleEnd}
                    onHintRevealed={() => {
                      hintsUsedRef.current += 1;
                    }}
                    onSolutionRevealed={() => {
                      hintsUsedRef.current += 1;
                      void scoreAttempt(attemptedMovesRef.current);
                    }}
                    apiRef={boardApi}
                  />
                </div>
              )}
              <div ref={settingsRef} className="absolute right-2 top-2 z-30 xl:left-full xl:right-auto xl:top-0 xl:ml-[2px]">
                <button
                  type="button"
                  onClick={() => setIsSettingsOpen((open) => !open)}
                  className={`relative flex h-7 w-7 items-center justify-center rounded-md border text-[#f0e0c0] transition hover:scale-105 active:scale-95 ${
                    isSettingsOpen
                      ? 'border-[#d9b87c]/70 text-[#f7e5c6]'
                      : 'border-black/60 hover:border-[#d9b87c]/45'
                  }`}
                  style={{
                    borderRadius: '4px',
                    background:
                      'linear-gradient(rgba(0,0,0,0.5),rgba(0,0,0,0.5)), url(/walnut-dark.webp)',
                    backgroundSize: 'cover',
                    backgroundPosition: 'center',
                    boxShadow:
                      '0 0 0 2px #1a0a02, inset 0 2px 0 rgba(255,200,100,0.12), inset 0 -2px 0 rgba(0,0,0,0.5), 0 4px 12px rgba(0,0,0,0.5)',
                  }}
                  aria-label="Puzzle settings"
                  aria-haspopup="dialog"
                  aria-expanded={isSettingsOpen}
                  title="Puzzle settings"
                >
                  <SettingsIcon />
                </button>

                {isSettingsOpen && (
                  <div
                    role="dialog"
                    aria-label="Puzzle settings"
                    className="absolute right-0 top-full mt-2 w-60 rounded-xl border border-[#d9b87c]/30 bg-[#1b120d]/95 p-3 text-white shadow-[0_18px_42px_rgba(0,0,0,0.52),inset_0_1px_0_rgba(255,255,255,0.08)] backdrop-blur-xl"
                  >
                    <div className="flex items-center justify-between border-b border-white/10 pb-2">
                      <span className="text-[10px] font-bold uppercase tracking-[0.24em] text-[#f7e5c6]/65">
                        Puzzle settings
                      </span>
                      <span className="text-[9px] font-bold uppercase tracking-[0.2em] text-[#37be7e]">
                        {autoAdvance ? 'Auto' : 'Manual'}
                      </span>
                    </div>

                    <div className="mt-3 flex items-center justify-between gap-4">
                      <div>
                        <div className="text-sm font-semibold text-[#f7e5c6]">Auto-advance</div>
                        <div className="mt-1 text-[11px] leading-4 text-white/45">
                          Continue after the puzzle ends
                        </div>
                      </div>
                      <button
                        type="button"
                        onClick={() => setAutoAdvance((enabled) => !enabled)}
                        className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors ${
                          autoAdvance ? 'bg-[#10b981]' : 'bg-white/15'
                        }`}
                        aria-pressed={autoAdvance}
                        aria-label="Toggle auto-advance"
                      >
                        <span
                          className={`inline-block h-5 w-5 rounded-full bg-white shadow transition-transform ${
                            autoAdvance ? 'translate-x-5' : 'translate-x-0.5'
                          }`}
                        />
                      </button>
                    </div>

                    <div className="mt-3 border-t border-white/10 pt-2 text-[10px] text-white/40">
                      {autoAdvance
                        ? 'Next puzzle loads after a short pause.'
                        : 'Use Next Puzzle when you are ready.'}
                    </div>
                  </div>
                )}
              </div>
            </div>
            {current && (
              <div className="mx-auto mt-3 flex w-full max-w-[calc(100vh-70px)] items-center justify-between text-xs text-white/50">
                <span>Move {current.puzzle.move_number}</span>
                {current.puzzle.game_url && (
                  <a
                    href={current.puzzle.game_url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 transition hover:text-white"
                  >
                    View game <ExternalLinkIcon />
                  </a>
                )}
              </div>
            )}
          </section>

          {/* ============ RIGHT: STATUS + ACTIONS ============ */}
          <section className="order-3 mx-auto flex w-full max-w-[420px] flex-col gap-5 xl:order-none xl:mx-0 xl:mt-0 xl:max-w-none xl:min-h-0">
            <div className="wooden-scroll xl:min-h-0 xl:overflow-y-auto">
              {current && (
                <PuzzleStatusPanel
                  sideToMoveLabel={getSideToMoveLabel(
                    current.puzzle.fen_before
                  )}
                  themeLabel={themeLabel}
                  result={result}
                  onHint={() => boardApi.current?.showHint()}
                  onShowSolution={() => boardApi.current?.showSolution()}
                />
              )}
            </div>

            <button
              type="button"
              onClick={advance}
              className={`${PUZZLE_CARD_CLASS} flex h-14 w-full shrink-0 items-center justify-center gap-3 text-sm font-semibold text-white transition hover:bg-white/5`}
            >
              Next Puzzle
            </button>
          </section>
        </div>
        )}
      </div>
    </div>
  );
}
