'use client';

import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { Chess, Square } from 'chess.js';
import dynamic from 'next/dynamic';
import type { PieceDropHandlerArgs, SquareRenderer } from 'react-chessboard';

import ReviewShell from '@/components/review/ReviewShell';

const Chessboard = dynamic(
  () => import('react-chessboard').then((module) => module.Chessboard),
  {
    ssr: false,
    loading: () => (
      <div className="flex h-full w-full items-center justify-center rounded-[8px] bg-black/35 text-sm text-[#f7e5c6]/65">
        Loading board
      </div>
    ),
  }
);

type BotSource =
  | 'ready'
  | 'in_book'
  | 'playing_naturally'
  | 'thinking'
  | 'error';

type TimeClassKey = 'rapid' | 'blitz' | 'bullet' | 'classical' | 'daily';

type OpponentProfile = {
  provider: 'lichess' | 'chesscom';
  opponent_username: string;
  game_count: number;
  rating: number;
  avatar_url: string | null;
  verified: boolean;
  ratings_by_time_class: Partial<Record<TimeClassKey, number>> | null;
  playing_style: 'Passive' | 'Balanced' | 'Aggressive' | null;
  preferred_time_control: string | null;
  time_control_distribution: Record<string, number> | null;
  opening_results: Record<string, unknown> | null;
  openings_lost_against: {
    name: string;
    family: string;
    color: 'white' | 'black' | null;
    raw_games: number;
    raw_wins: number | null;
    raw_losses: number | null;
    raw_draws: number | null;
    loss_rate: number;
    low_sample: boolean;
    /** True on rows projected from a pre-raw snapshot (weighted estimate). */
    legacy?: boolean;
  }[];
  traps: OpponentTrap[];
};

type OpponentTrap = {
  position_key: string;
  fen: string;
  moves: string[];
  classification: 'mistake' | 'blunder';
  game_count: number;
  move_number_min: number;
  move_number_max: number;
  example_game_id: string;
  example_ply: number;
  example_move_san: string;
  example_classification: 'mistake' | 'blunder';
  tier: 'position';
  /** True when the trap clears the bot's exploitability bar (shown for
   *  prep either way; only exploitable traps steer sparring). */
  exploitable: boolean;
};

type SparringMoveResponse = {
  move_uci: string;
  move_san: string;
  source: 'in_book' | 'playing_naturally';
  opponent_elo: number;
  opponent_elo_estimated: boolean;
  user_elo?: number | null;
  repertoire_frequency?: number | null;
};

type OpeningGameBlunder = {
  move_number: number;
  ply: number;
  move_san: string;
  classification: 'mistake' | 'blunder';
  position_key: string;
};

type OpeningGameSummary = {
  game_id: string;
  result: 'win' | 'loss' | 'draw' | '*';
  end_time: number;
  time_class: string;
  analyzed: boolean;
  first_blunder: OpeningGameBlunder | null;
};

type OpeningGamesResponse = {
  initial_game_id: string;
  games: OpeningGameSummary[];
};

type OpeningGame = {
  game_id: string;
  game_url: string;
  pgn: string;
  white: string;
  black: string;
  result: string;
  end_time: number;
  time_class: string;
  blunders: OpeningGameBlunder[];
};

type ApiErrorResponse = {
  detail?: string;
  error?: string;
};

type Premove = {
  from: string;
  to: string;
};

const START_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';

// Traps panel: poll the Stockfish blunder-analysis job while it is still
// working through the opponent's corpus. 6s keeps the progress line moving
// without hammering the status endpoint (backend limit: 30/min).
const ANALYSIS_POLL_INTERVAL_MS = 6000;

// The import job flips to "completed" (which triggers the modal redirect)
// a few seconds BEFORE try_start_opponent_analysis creates the analysis
// job row, so the traps panel can legitimately see a 404 right after an
// import. Retry for ~20s before falling back to the plain empty state.
const ANALYSIS_JOB_WAIT_ATTEMPTS = 8;
const ANALYSIS_JOB_WAIT_INTERVAL_MS = 2500;

// One-shot handoff from the import modal (see TrainPageClient): non-fatal
// import warnings such as a skipped Chess.com monthly archive.
const IMPORT_WARNINGS_STORAGE_KEY = 'praxis:opponent-import-warnings';

type AnalysisStatusResponse = {
  status: 'idle' | 'running' | 'complete';
  analyzed_games: number;
  total_games: number;
  heartbeat_at: string | null;
};

// 'checking' is the brief first status fetch; 'polling' means analyzed <
// total; 'complete' means every analyzed game is in and traps were
// refreshed; 'idle' is the no-job/total-0 fallback; 'failed' is the
// visible error state (analysis never started, or the worker died -
// previously both collapsed silently into 'idle').
type TrapsPhase = 'idle' | 'checking' | 'polling' | 'complete' | 'failed';

// A 'running' job whose heartbeat is older than this is a dead worker, not
// a slow one: the backend reclaims stale heartbeats after 5 minutes, so
// twice that (plus client/server clock skew) means no worker is alive.
const ANALYSIS_HEARTBEAT_STALE_MS = 10 * 60 * 1000;

const woodBoxStyle: React.CSSProperties = {
  borderRadius: '4px',
  background:
    'linear-gradient(rgba(0,0,0,0.5),rgba(0,0,0,0.5)), url(/walnut-dark.webp)',
  backgroundSize: 'cover',
  backgroundPosition: 'center',
  boxShadow:
    '0 0 0 2px #1a0a02, inset 0 2px 0 rgba(255,200,100,0.12), inset 0 -2px 0 rgba(0,0,0,0.5), 0 4px 12px rgba(0,0,0,0.5)',
};

const panelClass =
  'flex h-full flex-col gap-4 overflow-hidden rounded-[24px] border border-black/50 p-4 [background-image:linear-gradient(rgba(0,0,0,0.55),rgba(0,0,0,0.55)),url(/walnut-dark.webp)] [background-size:cover] [background-position:center] [box-shadow:0_10px_30px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(255,255,255,0.06),inset_0_-1px_0_rgba(0,0,0,0.5)]';

const rightPanelClass =
  'flex h-full min-h-0 flex-col gap-4 overflow-hidden rounded-[24px] border border-black/50 [background-image:linear-gradient(rgba(0,0,0,0.55),rgba(0,0,0,0.55)),url(/walnut-dark.webp)] [background-size:cover] [background-position:center] [box-shadow:0_10px_30px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(255,255,255,0.06),inset_0_-1px_0_rgba(0,0,0,0.5)]';

// In-game right card: the button language of the engine sparring page
// (SparringGame), rendered directly on the card surface - no nested boxes.

// Endgame Trainer's gold primary assist (Hint), compacted for a 3-up row.
const GOLD_BUTTON_CLASS =
  'flex w-full items-center justify-center gap-1.5 rounded-xl bg-gradient-to-b from-[#eacb90] to-[#c1954f] px-2 py-3 text-xs font-bold text-[#2a1a06] shadow-[0_10px_22px_rgba(0,0,0,0.45),inset_0_1px_0_rgba(255,255,255,0.55)] transition hover:from-[#f2d8a4] hover:to-[#cda261] disabled:cursor-not-allowed disabled:opacity-50';

// Endgame Trainer's secondary action, compacted for a 3-up row.
const SECONDARY_BUTTON_CLASS =
  'flex w-full items-center justify-center gap-1.5 rounded-xl border border-white/15 bg-white/5 px-2 py-3 text-xs font-bold text-white/80 transition hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-50';

// Resign reads as a danger action, not a neutral one.
const DANGER_BUTTON_CLASS =
  'flex w-full items-center justify-center gap-1.5 rounded-xl border border-red-400/25 bg-red-400/10 px-2 py-3 text-xs font-bold text-red-200 transition hover:bg-red-400/20 disabled:cursor-not-allowed disabled:opacity-50';

// The result modal's actions use the FULL-size button language; the in-game
// row above uses the compacted variants. Same as the engine sparring page.
const MODAL_PRIMARY_BUTTON_CLASS =
  'flex w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-b from-[#eacb90] to-[#c1954f] px-4 py-3.5 text-sm font-bold text-[#2a1a06] shadow-[0_10px_22px_rgba(0,0,0,0.45),inset_0_1px_0_rgba(255,255,255,0.55)] transition hover:from-[#f2d8a4] hover:to-[#cda261]';

const MODAL_SECONDARY_BUTTON_CLASS =
  'flex w-full items-center justify-center gap-2 rounded-xl border border-white/15 bg-white/5 px-4 py-3.5 text-sm font-bold text-white/80 transition hover:bg-white/10';

// The modal card: same walnut language as the engine sparring result modal.
const MODAL_CARD_CLASS =
  'rounded-2xl border border-black/50 backdrop-blur-sm [background-image:linear-gradient(rgba(0,0,0,0.5),rgba(0,0,0,0.5)),url(/walnut-dark.webp)] [background-size:cover] [background-position:center] [box-shadow:0_10px_30px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(255,255,255,0.06),inset_0_-1px_0_rgba(0,0,0,0.5)]';

type GameOutcome = { title: string; subtitle: string };

// The Puzzles / Endgame Trainer hint language, verbatim: only the piece to
// move is highlighted, in emerald, and it fades on its own. The destination
// is deliberately withheld.
const HIGHLIGHT_HINT = 'rgba(16, 185, 129, 0.4)';
const HINT_FADE_MS = 4000;

type SparringHistoryMove = {
  san: string;
  color: 'w' | 'b';
  fen: string;
};

type PrewarmLine = {
  move_uci?: string | null;
  move_san?: string | null;
};

const modalCardClass =
  'relative m-auto w-full max-w-md rounded-[24px] border border-black/50 p-4 [background-image:linear-gradient(rgba(0,0,0,0.55),rgba(0,0,0,0.55)),url(/walnut-dark.webp)] [background-size:cover] [background-position:center] [box-shadow:0_10px_30px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(255,255,255,0.06),inset_0_-1px_0_rgba(0,0,0,0.5)]';

// Square-board sizing shared by the modals, so the whole card fits inside
// the viewport without scrolling. Chrome below/above the board measures
// ~13.6rem: overlay py-6 (3rem) + card p-4 (2rem) + title (1.75rem) +
// board mt-3 (0.75rem) + control bar h-7 (1.75rem) + info mt-2 + one 11px
// line (~1.5rem) + blunder mt-3 + row (~2.9rem). 15rem leaves ~1.4rem of
// slack for wrapped lines; 26rem is the card content width (max-w-md 28rem
// minus p-4 2rem). So the board is min(content width, viewport - 15rem).
const modalBoardWidthClass =
  'mx-auto w-full max-w-[min(26rem,calc(100dvh_-_15rem))]';

// Read-only wood board options shared by the trap position viewer and the
// opening replay viewer.
const modalBoardOptions = {
  allowDragging: false,
  animationDurationInMs: 0,
  boardStyle: { width: '100%', height: '100%', borderRadius: '0' },
  darkSquareStyle: {
    backgroundImage: 'url(/walnut-dark.webp)',
    backgroundSize: '110% 110%',
    backgroundPosition: 'center',
  },
  lightSquareStyle: {
    backgroundImage: 'url(/walnut-light.webp)',
    backgroundSize: '110% 110%',
    backgroundPosition: 'center',
  },
  darkSquareNotationStyle: { color: '#f0e0c0' },
  lightSquareNotationStyle: { color: '#3a2410' },
};

const STYLE_PILL: Record<
  'Passive' | 'Balanced' | 'Aggressive',
  { ring: string; bg: string; text: string }
> = {
  Passive: {
    ring: 'border-zinc-500/40',
    bg: 'bg-zinc-500/10',
    text: 'text-zinc-300',
  },
  Balanced: {
    ring: 'border-amber-500/40',
    bg: 'bg-amber-500/10',
    text: 'text-amber-300',
  },
  Aggressive: {
    ring: 'border-rose-500/40',
    bg: 'bg-rose-500/10',
    text: 'text-rose-300',
  },
};

// Time-control slice colors. Matches the donut legend swatches and is
// used for the donut arc fill. The "Other" bucket falls back to slate.
const TC_COLOR: Record<string, string> = {
  '3+2': '#10b981',
  '10+0': '#f59e0b',
  '1+0': '#8b5cf6',
  Other: '#6b7280',
};

const DEFAULT_TC_COLOR = '#9ca3af';

// Time-class row labels + icon mapping for the per-class rating grid.
const TIME_CLASS_META: Record<
  TimeClassKey,
  {
    label: string;
    // Rating-card icon (large, self-contained praxis tile art).
    icon: (className: string) => React.ReactNode;
    // Time Control picker icon (small, transparent flat variant so it sits
    // on the pill backgrounds). Falls back to `icon` when absent.
    controlIcon?: (className: string) => React.ReactNode;
    // Selected-pill styling. Falls back to the cream default when absent.
    selectedClass?: string;
    tone: string;
  }
> = {
  rapid: {
    label: 'Rapid',
    tone: 'text-emerald-300',
    // eslint-disable-next-line @next/next/no-img-element -- static public SVG icon
    icon: (className) => <img src="/praxis-rapid.svg" alt="" className={className} />,
    // eslint-disable-next-line @next/next/no-img-element -- static public SVG icon
    controlIcon: (className) => <img src="/rapid.svg" alt="" className={className} />,
    // Same green treatment as the Start Game button.
    selectedClass:
      'border-[#10b981]/40 bg-[#10b981]/15 text-[#a7f3d0] shadow-[0_8px_24px_rgba(16,185,129,0.15)]',
  },
  blitz: {
    label: 'Blitz',
    tone: 'text-amber-300',
    // eslint-disable-next-line @next/next/no-img-element -- static public SVG icon
    icon: (className) => <img src="/praxis-blitz.svg" alt="" className={className} />,
    // eslint-disable-next-line @next/next/no-img-element -- static public SVG icon
    controlIcon: (className) => <img src="/blitz.svg" alt="" className={className} />,
  },
  bullet: {
    label: 'Bullet',
    tone: 'text-violet-300',
    // eslint-disable-next-line @next/next/no-img-element -- static public SVG icon
    icon: (className) => <img src="/praxis-bullet.svg" alt="" className={className} />,
    // eslint-disable-next-line @next/next/no-img-element -- static public SVG icon
    controlIcon: (className) => <img src="/bullet.svg" alt="" className={className} />,
    // Same effect as the Start Game button, in brown.
    selectedClass:
      'border-[#b07a45]/50 bg-[#b07a45]/20 text-[#f0d3a8] shadow-[0_8px_24px_rgba(176,122,69,0.22)]',
  },
  classical: {
    label: 'Classical',
    tone: 'text-sky-300',
    icon: (className) => (
      <svg className={className} viewBox="0 0 24 24" fill="currentColor" aria-hidden>
        <path d="M12 2 4 5v6c0 4.5 3.4 8.7 8 11 4.6-2.3 8-6.5 8-11V5l-8-3Z" />
      </svg>
    ),
  },
  daily: {
    label: 'Daily',
    tone: 'text-stone-300',
    icon: (className) => (
      <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <rect x="3" y="4" width="18" height="17" rx="2" />
        <path d="M3 9h18" />
        <path d="M8 2v4" />
        <path d="M16 2v4" />
      </svg>
    ),
  },
};

const TIME_CLASS_ORDER: TimeClassKey[] = ['rapid', 'blitz', 'bullet'];

// Map a distribution label ("3+2") or canonical bucket name to a coarse
// time class for the picker. Mirrors the backend's base + inc*40 heuristic;
// classical maps to rapid (the slowest option the picker offers).
function timeClassFromLabel(label: string | null | undefined): TimeClassKey | null {
  if (!label) {
    return null;
  }
  const key = label.trim().toLowerCase();
  if (key === 'rapid' || key === 'blitz' || key === 'bullet') {
    return key;
  }
  const match = /^(\d+)(?:\+(\d+))?$/.exec(key);
  if (!match) {
    return null;
  }
  const base = Number(match[1]);
  const increment = Number(match[2] ?? 0);
  const seconds = base < 60 ? base * 60 : base;
  const estimated = seconds + increment * 40;
  if (estimated < 180) {
    return 'bullet';
  }
  if (estimated < 600) {
    return 'blitz';
  }
  return 'rapid';
}

export default function OpponentPrepPage() {
  const [profiles, setProfiles] = useState<OpponentProfile[]>([]);
  const [selectedKey, setSelectedKey] = useState('');
  const [humanColor, setHumanColor] = useState<'white' | 'black'>('white');
  const [game, setGame] = useState(() => new Chess());
  const [viewIndex, setViewIndex] = useState(0);
  const [resigned, setResigned] = useState(false);
  const [isStarted, setIsStarted] = useState(false);
  const [isThinking, setIsThinking] = useState(false);
  const [status, setStatus] = useState<BotSource>('ready');
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selectedSquare, setSelectedSquare] = useState<string | null>(null);
  const [premove, setPremove] = useState<Premove | null>(null);
  const [importWarnings, setImportWarnings] = useState<string[]>([]);
  const [timeControl, setTimeControl] = useState<TimeClassKey | ''>('');
  // Two-press hint, same as engine sparring: the first press highlights only
  // the piece to move (it fades on its own); the fetched move is kept so a
  // second press plays it.
  const [hint, setHint] = useState<{
    from: string;
    to: string;
    promotion?: string;
  } | null>(null);
  const [hintVisible, setHintVisible] = useState(false);
  const [isHintLoading, setIsHintLoading] = useState(false);
  const [hintsUsed, setHintsUsed] = useState(0);
  // The end-of-game modal: shown when the game resolves (checkmate, draw or
  // resignation), dismissable so the final position and move list stay
  // reviewable. Same modal as the engine sparring page.
  const [showResult, setShowResult] = useState(false);
  // The left setup panel collapses once the game starts so the board and the
  // moves card take the stage; it expands again when leaving to prep.
  const [leftCollapsed, setLeftCollapsed] = useState(false);
  const gameRef = useRef(game);
  const botMoveInFlightRef = useRef(false);
  const moveListRef = useRef<HTMLDivElement>(null);
  const hintTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Latest viewed FEN, for discarding hint answers that land after the board
  // has moved on. Bumped on every fresh start (and every undo): answers from
  // the previous line are discarded instead of landing on the new board.
  const viewFenRef = useRef(START_FEN);
  const gameIdRef = useRef(0);
  // A resignation ends the game immediately, so a bot reply already in
  // flight must not land on the final position.
  const resignedRef = useRef(false);
  // Every position key since the game started. The game is rebuilt from a
  // FEN on each ply, which resets chess.js's own repetition counter, so
  // threefold repetition is counted from these keys instead. Reset with the
  // game in startGame.
  const positionKeysRef = useRef<string[]>([positionKey(START_FEN)]);

  useEffect(() => {
    gameRef.current = game;
  }, [game]);

  useEffect(() => {
    resignedRef.current = resigned;
  }, [resigned]);

  useEffect(() => {
    let isCancelled = false;

    async function loadOpponents() {
      const pageLoadRequestStarted = performance.now();
      try {
        const response = await fetch('/api/train/opponents', { cache: 'no-store' });
        if (!response.ok) {
          const body = (await response.json().catch(() => ({}))) as ApiErrorResponse;
          throw new Error(body.detail ?? body.error ?? `Opponent request failed (${response.status})`);
        }
        const data = (await response.json()) as { opponents: OpponentProfile[] };
        const gameCount = data.opponents.reduce(
          (total, profile) => total + profile.game_count,
          0
        );
        console.info(
          `[IMPORT_PROFILE] phase=page_load_request duration_ms=${(
            performance.now() - pageLoadRequestStarted
          ).toFixed(2)} games=${gameCount} location=browser`
        );
        if (!isCancelled) {
          setProfiles(data.opponents);
          setSelectedKey((current) => current || profileKey(data.opponents[0]));
        }
      } catch (error) {
        if (!isCancelled) {
          setLoadError(error instanceof Error ? error.message : 'Failed to load opponents.');
        }
      }
    }

    loadOpponents();
    return () => {
      isCancelled = true;
    };
  }, []);

  // Read-and-clear the one-shot import warnings handed off by the modal
  // so an incomplete corpus (e.g. a skipped Chess.com archive) is visible
  // on the panels it affects instead of silently looking fully loaded.
  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(IMPORT_WARNINGS_STORAGE_KEY);
      if (!raw) {
        return;
      }
      sessionStorage.removeItem(IMPORT_WARNINGS_STORAGE_KEY);
      const parsed: unknown = JSON.parse(raw);
      if (Array.isArray(parsed)) {
        setImportWarnings(
          parsed.filter((item): item is string => typeof item === 'string')
        );
      }
    } catch {
      // Malformed payload or storage unavailable - nothing to show.
    }
  }, []);

  const selectedProfile = useMemo(
    () => profiles.find((profile) => profileKey(profile) === selectedKey) ?? null,
    [profiles, selectedKey]
  );
  // Remount the traps panel when the selected opponent changes so its
  // polling state resets without a set-state-in-effect.
  const trapsPanelKey = selectedProfile
    ? `${selectedProfile.provider}:${selectedProfile.opponent_username.toLowerCase()}`
    : 'none';
  const botColor = humanColor === 'white' ? 'black' : 'white';

  // Prefill the Time Control picker when the selected profile changes.
  // preferred_time_control (the recency-weighted most-common bucket) is
  // mapped to its coarse class; falls back to the first key of the
  // distribution when only that is available. Empty string leaves the
  // picker unselected.
  useEffect(() => {
    const preferred = selectedProfile?.preferred_time_control;
    const firstDistribution = selectedProfile?.time_control_distribution
      ? Object.keys(selectedProfile.time_control_distribution)[0]
      : null;
    setTimeControl(
      timeClassFromLabel(preferred) ?? timeClassFromLabel(firstDistribution) ?? ''
    );
  }, [selectedProfile]);

  // checkmate | stalemate | insufficient material | fifty-move rule |
  // threefold repetition (the last one via the tracked keys -- see
  // detectGameEnding) or resignation. Any of them ends the game.
  const ending = detectGameEnding(game, positionKeysRef.current);
  const gameOver = ending !== null;
  const over = gameOver || resigned;

  // Verbose history is the single source for the move list, the replayed
  // positions and whose turn it is at any of them (chess.js Move carries
  // the SAN, the mover's color and the FEN AFTER the move).
  const history = useMemo<SparringHistoryMove[]>(
    () =>
      game.history({ verbose: true }).map((entry) => ({
        san: entry.san,
        color: entry.color,
        fen: entry.after,
      })),
    [game]
  );

  // Clamped on read: transient states (a premove landing, an undone line, a
  // reset racing an in-flight reply) must never index past the history.
  const safeViewIndex = Math.max(0, Math.min(viewIndex, history.length));

  const viewFen =
    safeViewIndex === 0 ? START_FEN : (history[safeViewIndex - 1]?.fen ?? START_FEN);
  useEffect(() => {
    viewFenRef.current = viewFen;
  }, [viewFen]);
  // A read-only Chess for the viewed position: click-selection legality
  // answers for the position ON the board, which is the reviewed one while
  // browsing and the live one otherwise.
  const viewGame = useMemo(() => new Chess(viewFen), [viewFen]);
  const viewTurn: 'w' | 'b' =
    safeViewIndex === 0 ? 'w' : (history[safeViewIndex - 1]?.color ?? 'w') === 'w' ? 'b' : 'w';
  const isViewingLive = safeViewIndex === history.length;
  const humanCanMove =
    isStarted &&
    !isThinking &&
    !over &&
    (viewTurn === 'w' ? 'white' : 'black') === humanColor;

  // Premoving: while the bot is on the move the user may commit a move that
  // is played automatically (when still legal) the moment it becomes their
  // turn, so sparring keeps a bullet-like rhythm. Live view only: reviewing
  // an older position is read-only navigation.
  const canPremove = isStarted && !over && !humanCanMove && isViewingLive;
  const humanPieceColor = humanColor === 'white' ? 'w' : 'b';

  // Probe position with the turn flipped to the human. Used to validate
  // premove shapes and show target hints while the bot is to move. Null
  // whenever the human is on turn (normal play) or the game is not running.
  const premoveProbeGame = useMemo<Chess | null>(() => {
    if (!canPremove) {
      return null;
    }
    try {
      const parts = game.fen().split(' ');
      parts[1] = humanPieceColor;
      return new Chess(parts.join(' '));
    } catch {
      return null;
    }
  }, [canPremove, game, humanPieceColor]);

  const requestBotMove = useCallback(async () => {
    if (!selectedProfile || botMoveInFlightRef.current || gameRef.current.isGameOver() || resignedRef.current) {
      return;
    }

    botMoveInFlightRef.current = true;
    setIsThinking(true);
    setStatus('thinking');

    const pliesBefore = gameRef.current.history().length;
    const gameId = gameIdRef.current;

    try {
      const response = await fetch('/api/train/sparring-move', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          provider: selectedProfile.provider,
          opponent_username: selectedProfile.opponent_username,
          fen: gameRef.current.fen(),
          move_history: gameRef.current.history(),
          bot_color: botColor,
          time_control: timeControl || undefined,
        }),
      });

      if (!response.ok) {
        const body = (await response.json().catch(() => ({}))) as ApiErrorResponse;
        throw new Error(body.detail ?? body.error ?? `Move request failed (${response.status})`);
      }

      const data = (await response.json()) as SparringMoveResponse;
      // A reset replaced the game, or the player resigned, while this
      // reply was in flight: drop it rather than mutating a finished board.
      if (gameIdRef.current !== gameId || resignedRef.current) {
        return;
      }
      // Replay the full line: building from a FEN starts history empty, and
      // the move list / review navigation depend on the complete SAN line.
      const nextGame = replaySans(gameRef.current.history());
      nextGame.move(uciToMove(data.move_uci));
      positionKeysRef.current.push(positionKey(nextGame.fen()));
      setGame(nextGame);
      // Follow the reply only when the player is watching the live position;
      // reviewing an earlier move keeps their place.
      setViewIndex((prev) => (prev === pliesBefore ? pliesBefore + 1 : prev));
      setStatus(data.source);
    } catch {
      // The board simply stays put; Undo re-arms the bot for a retry.
      setStatus('error');
    } finally {
      // Only the current game owns the thinking flag: a stale reply from the
      // previous game must not unlock the new game's board mid-request.
      if (gameIdRef.current === gameId) {
        botMoveInFlightRef.current = false;
        setIsThinking(false);
      }
    }
  }, [botColor, selectedProfile, timeControl]);

  // Play a stored premove as soon as it becomes the human's turn. Premoves
  // that stopped being legal (the bot blocked the path, captured the piece
  // or delivered check) are discarded, matching lichess/chess.com behaviour.
  useEffect(() => {
    if (!premove) {
      return;
    }
    if (!isStarted || over) {
      setPremove(null);
      return;
    }

    const turnColor = game.turn() === 'w' ? 'white' : 'black';
    if (turnColor !== humanColor) {
      return;
    }

    setPremove(null);

    // Same full-line replay as the bot reply: building from a FEN would
    // start history empty and break the move list.
    const nextGame = replaySans(game.history());
    let move: ReturnType<typeof nextGame.move> | null = null;
    try {
      move = nextGame.move({ from: premove.from, to: premove.to, promotion: 'q' });
    } catch {
      move = null;
    }

    if (move) {
      positionKeysRef.current.push(positionKey(nextGame.fen()));
      setGame(nextGame);
      // The premove only fires while watching live, so the view follows it.
      setViewIndex(nextGame.history().length);
      setStatus('ready');
      setSelectedSquare(null);
    }
  }, [game, over, humanColor, isStarted, premove]);

  useEffect(() => {
    if (!isStarted || !selectedProfile || over || isThinking || status === 'error') {
      return;
    }

    const turnColor = game.turn() === 'w' ? 'white' : 'black';
    if (turnColor === botColor) {
      requestBotMove();
    }
  }, [botColor, game, over, isStarted, isThinking, requestBotMove, selectedProfile, status]);

  // Keep the move box pinned to the newest move while the player is
  // following the live game; leave their scroll alone while reviewing.
  useEffect(() => {
    if (!isViewingLive) {
      return;
    }
    const list = moveListRef.current;
    if (list) {
      list.scrollTop = list.scrollHeight;
    }
  }, [isViewingLive, history.length]);

  // Reviewing an older position must not leak into the next position:
  // selection and hint highlights reset with the view.
  useEffect(() => {
    setSelectedSquare(null);
    setHint(null);
    setHintVisible(false);
  }, [viewIndex]);

  useEffect(() => {
    return () => {
      if (hintTimerRef.current) {
        clearTimeout(hintTimerRef.current);
      }
    };
  }, []);

  // The result modal pops the moment the game resolves, once per game; the
  // user can dismiss it to review the final position and move list.
  const outcomeName = selectedProfile?.opponent_username ?? 'Opponent';
  const resultOutcome = describeOutcome(game, resigned, humanColor, outcomeName);
  useEffect(() => {
    if (over) {
      setShowResult(true);
    }
  }, [over]);

  function startGame() {
    const nextGame = new Chess();
    positionKeysRef.current = [positionKey(nextGame.fen())];
    gameIdRef.current += 1;
    if (hintTimerRef.current) {
      clearTimeout(hintTimerRef.current);
      hintTimerRef.current = null;
    }
    setGame(nextGame);
    setViewIndex(0);
    setResigned(false);
    setIsStarted(true);
    setStatus('ready');
    setSelectedSquare(null);
    setPremove(null);
    setHint(null);
    setHintVisible(false);
    setIsHintLoading(false);
    setHintsUsed(0);
    setShowResult(false);
    // The setup panel steps aside so the board and the moves card take the
    // stage; the rail arrow brings it back anytime.
    setLeftCollapsed(true);

    // Fire-and-forget session warmup: while the user plays their first
    // moves, the backend indexes any missing repertoire rows and
    // precomputes the opponent's style/traps profile, so the first
    // (out-of-book) Maia reply is served from cache instead of paying a
    // multi-second corpus-replay miss. Failures are intentionally ignored
    // The move endpoint degrades gracefully and retries the cache itself.
    if (selectedProfile) {
      void fetch('/api/train/sparring-warmup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          provider: selectedProfile.provider,
          opponent_username: selectedProfile.opponent_username,
          time_control: timeControl || undefined,
        }),
      }).catch(() => {});
    }
  }

  function tryMove(sourceSquare: string, targetSquare: string, promotion = 'q'): boolean {
    if (!humanCanMove) {
      return false;
    }

    if (sourceSquare === targetSquare) {
      return false;
    }

    // Moving from a reviewed position branches: the future after that move
    // is discarded and the game continues from the new choice.
    const baseSans = gameRef.current.history().slice(0, viewIndex);
    const nextGame = replaySans(baseSans);
    let move: ReturnType<typeof nextGame.move> | null = null;
    try {
      move = nextGame.move({
        from: sourceSquare,
        to: targetSquare,
        promotion,
      });
    } catch {
      return false;
    }

    if (!move) {
      return false;
    }

    positionKeysRef.current = [
      ...positionKeysRef.current.slice(0, viewIndex + 1),
      positionKey(nextGame.fen()),
    ];
    setGame(nextGame);
    setViewIndex(baseSans.length + 1);
    setStatus('ready');
    setSelectedSquare(null);
    setPremove(null);
    setHint(null);
    setHintVisible(false);
    return true;
  }

  function goToIndex(ply: number) {
    setViewIndex(ply);
    setSelectedSquare(null);
    setHint(null);
    setHintVisible(false);
  }

  function resignGame() {
    if (over) {
      return;
    }
    setResigned(true);
    setSelectedSquare(null);
    setPremove(null);
    setHint(null);
    setHintVisible(false);
  }

  // Same settings, fresh board (the result modal's "Rematch").
  function rematch() {
    startGame();
  }

  // Leave the finished (or idle) game and return to the prep overview with
  // the setup panel expanded (the result modal's secondary action).
  function exitToPrep() {
    positionKeysRef.current = [positionKey(START_FEN)];
    gameIdRef.current += 1;
    if (hintTimerRef.current) {
      clearTimeout(hintTimerRef.current);
      hintTimerRef.current = null;
    }
    botMoveInFlightRef.current = false;
    setGame(new Chess());
    setViewIndex(0);
    setResigned(false);
    setIsStarted(false);
    setStatus('ready');
    setIsThinking(false);
    setSelectedSquare(null);
    setPremove(null);
    setHint(null);
    setHintVisible(false);
    setIsHintLoading(false);
    setHintsUsed(0);
    setShowResult(false);
    setLeftCollapsed(false);
  }

  function undoMove() {
    if (isThinking || over || history.length === 0) {
      return;
    }

    // Take back to the player's previous decision point: the bot's reply
    // goes with the player's move, a lone un-answered player move goes alone.
    const lastPlyWasPlayer =
      (history.length - 1) % 2 === (humanColor === 'white' ? 0 : 1);
    const target = Math.max(0, history.length - (lastPlyWasPlayer ? 1 : 2));
    if (target === history.length) {
      return;
    }

    const rebuilt = buildGameAt(
      history.map((entry) => entry.san),
      target
    );
    // A bot reply fetched before the undo must not land on the rewound game.
    gameIdRef.current += 1;
    positionKeysRef.current = positionKeysRef.current.slice(0, target + 1);
    setGame(rebuilt);
    setViewIndex(target);
    setSelectedSquare(null);
    setPremove(null);
    setHint(null);
    setHintVisible(false);
    setStatus('ready');
    botMoveInFlightRef.current = false;
  }

  async function requestHint() {
    if (!humanCanMove || isHintLoading) {
      return;
    }

    // Second press on a position whose hint was already fetched: play it.
    // The first press only points at the piece; this second press is the
    // "show move" assist.
    if (hint) {
      if (hintTimerRef.current) {
        clearTimeout(hintTimerRef.current);
        hintTimerRef.current = null;
      }
      const { from, to, promotion } = hint;
      setHint(null);
      setHintVisible(false);
      tryMove(from, to, promotion);
      return;
    }

    setIsHintLoading(true);

    // The live sandbox's best line for the side to move (the human here):
    // no Elo or persona needed, unlike the sparring bot endpoint.
    const pathSans = gameRef.current.history().slice(0, viewIndex);
    const fenBefore = viewFen;
    const gameId = gameIdRef.current;

    try {
      const response = await fetch('/api/review/live/prewarm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ moves: pathSans, expected_mode: null }),
      });
      if (!response.ok) {
        throw new Error(`Hint request failed (${response.status})`);
      }
      const data = (await response.json()) as {
        best?: PrewarmLine | null;
        second_best?: PrewarmLine | null;
      };
      const line =
        [data.best, data.second_best].find((entry) => entry?.move_uci) ?? null;
      const uci = line?.move_uci ?? null;
      if (!uci || uci.length < 4) {
        throw new Error('No hint available for this position.');
      }

      // A slow answer can land after the board has moved on (or a reset
      // replaced it): a hint for a position that is no longer on the board
      // is never shown.
      if (gameIdRef.current !== gameId || viewFenRef.current !== fenBefore) {
        return;
      }

      // Puzzles-style: only the piece to move is highlighted, and it fades
      // on its own. The destination is deliberately withheld; the fetched
      // move is retained so the next press can play it.
      if (hintTimerRef.current) {
        clearTimeout(hintTimerRef.current);
      }
      const parsed = uciToMove(uci);
      setHint(parsed);
      setHintVisible(true);
      setHintsUsed((count) => count + 1);
      hintTimerRef.current = setTimeout(() => {
        hintTimerRef.current = null;
        setHintVisible(false);
      }, HINT_FADE_MS);
    } catch {
      // The highlight simply never appears; pressing Hint again retries.
    } finally {
      setIsHintLoading(false);
    }
  }

  // Validate a premove against the turn-flipped probe position. Allows any
  // pseudo-legal shape for the human's pieces (including captures, castling
  // and promotions, which auto-queen) but rejects self-captures.
  function trySetPremove(from: string, to: string): boolean {
    if (!canPremove || !premoveProbeGame || from === to) {
      return false;
    }

    const fromPiece = gameRef.current.get(from as Square);
    if (!fromPiece || fromPiece.color !== humanPieceColor) {
      return false;
    }

    const toPiece = gameRef.current.get(to as Square);
    if (toPiece && toPiece.color === fromPiece.color) {
      return false;
    }

    try {
      const isLegalShape = premoveProbeGame
        .moves({ square: from as Square, verbose: true })
        .some((move) => move.to === to);
      if (isLegalShape) {
        setPremove({ from, to });
      }
      return isLegalShape;
    } catch {
      return false;
    }
  }

  function handleDrop({
    sourceSquare,
    targetSquare,
  }: {
    sourceSquare: string;
    targetSquare: string | null;
  }) {
    if (!targetSquare) {
      return false;
    }

    if (humanCanMove) {
      return tryMove(sourceSquare, targetSquare);
    }

    if (trySetPremove(sourceSquare, targetSquare)) {
      setSelectedSquare(null);
      return true;
    }

    return false;
  }

  function handleSquareRightClick() {
    setPremove(null);
  }

  function handleSquareClick({ square }: { piece: { pieceType: string } | null; square: string }) {
    if (humanCanMove) {
      const clickedPiece = viewGame.get(square as Square);
      const isOwnPiece = clickedPiece?.color === viewTurn;

      if (!selectedSquare) {
        setSelectedSquare(isOwnPiece ? square : null);
        return;
      }

      if (selectedSquare === square) {
        setSelectedSquare(null);
        return;
      }

      const legalMove = viewGame
        .moves({ square: selectedSquare as Square, verbose: true })
        .some((move) => move.to === square);

      if (legalMove) {
        tryMove(selectedSquare, square);
        return;
      }

      setSelectedSquare(isOwnPiece ? square : null);
      return;
    }

    if (!canPremove) {
      setSelectedSquare(null);
      return;
    }

    const clickedPiece = gameRef.current.get(square as Square);
    const isOwnPiece = clickedPiece?.color === humanPieceColor;

    // Clicking the destination of an existing premove cancels it.
    if (premove && square === premove.to) {
      setPremove(null);
      return;
    }

    // Clicking the origin of an existing premove re-opens it for editing.
    if (premove && square === premove.from) {
      setPremove(null);
      setSelectedSquare(square);
      return;
    }

    if (selectedSquare && selectedSquare !== square && trySetPremove(selectedSquare, square)) {
      setSelectedSquare(null);
      return;
    }

    setSelectedSquare(isOwnPiece ? square : null);
  }

  const highlightSquares = useMemo<Record<string, React.CSSProperties>>(() => {
    const squares: Record<string, React.CSSProperties> = {};
    // The piece-only hint sits under the selection tint: the user's own
    // selection always wins the square.
    const hintFrom = hintVisible ? hint?.from : null;
    if (hintFrom) {
      squares[hintFrom] = { backgroundColor: HIGHLIGHT_HINT };
    }
    if (premove) {
      const premoveStyle = { backgroundColor: 'rgba(56, 189, 248, 0.4)' };
      squares[premove.from] = premoveStyle;
      squares[premove.to] = premoveStyle;
    }
    if (selectedSquare) {
      squares[selectedSquare] = { backgroundColor: 'rgba(255, 170, 0, 0.35)' };
    }
    return squares;
  }, [hint, hintVisible, premove, selectedSquare]);

  // Pairs of plies for the in-game move list: White always opens, so index
  // 0 is White's move. Same as the engine sparring page.
  const moveRows = useMemo(() => {
    const rows: { number: number; startPly: number; white: string; black: string | null }[] = [];
    for (let ply = 0; ply < history.length; ply += 2) {
      rows.push({
        number: ply / 2 + 1,
        startPly: ply,
        white: history[ply].san,
        black: history[ply + 1]?.san ?? null,
      });
    }
    return rows;
  }, [history]);

  const hintSquares = useMemo<Record<string, 'dot' | 'ring'>>(() => {
    // Destination dots answer for the position ON the board (the viewed one
    // while browsing), never the live game behind it.
    const sourceGame = humanCanMove ? viewGame : premoveProbeGame;
    if (!selectedSquare || !sourceGame) {
      return {};
    }
    try {
      const legalMoves = sourceGame.moves({ square: selectedSquare as Square, verbose: true });
      const hints: Record<string, 'dot' | 'ring'> = {};
      for (const move of legalMoves) {
        const targetPiece = sourceGame.get(move.to as Square);
        hints[move.to] = targetPiece ? 'ring' : 'dot';
      }
      return hints;
    } catch {
      return {};
    }
  }, [viewGame, humanCanMove, premoveProbeGame, selectedSquare]);

  const squareRenderer = useCallback<SquareRenderer>(
    ({ square, children }) => {
      const hint = hintSquares[square];
      const squareStyle = highlightSquares[square];
      return (
        <div className="relative h-full w-full" style={squareStyle}>
          {hint === 'dot' && (
            <div className="pointer-events-none absolute left-1/2 top-1/2 h-[30%] w-[30%] -translate-x-1/2 -translate-y-1/2 rounded-full bg-black/25" />
          )}
          {hint === 'ring' && (
            <div className="pointer-events-none absolute left-1/2 top-1/2 h-[88%] w-[88%] -translate-x-1/2 -translate-y-1/2 rounded-full border-[6px] border-black/25" />
          )}
          {children}
        </div>
      );
    },
    [hintSquares, highlightSquares]
  );

  return (
    <div className="relative -mt-2 h-[calc(100vh-2.5rem)] w-full overflow-y-auto px-6 pb-1 pt-6 text-white lg:overflow-hidden lg:px-10 xl:pt-5 [background-image:url(/walnut-dark.webp)] [background-size:cover] [background-position:center]">
      <ReviewShell
        leftCollapsed={leftCollapsed}
        onLeftCollapsedChange={setLeftCollapsed}
        centerPair={isStarted}
        importPanel={
          <aside className={panelClass}>
            <ProfileCard
              profile={selectedProfile}
              loadError={loadError}
            />

            <RatingsRow ratings={selectedProfile?.ratings_by_time_class ?? null} />

            <PlayingStylePill style={selectedProfile?.playing_style ?? null} />

            <TimeClassSelect
              value={timeControl}
              onChange={setTimeControl}
              disabled={!selectedProfile || isStarted}
            />

            <PlayAsSelect
              value={humanColor}
              onChange={setHumanColor}
              disabled={!selectedProfile || isStarted}
            />

            <button
              type="button"
              onClick={startGame}
              disabled={!selectedProfile || isThinking}
              className="group flex h-14 w-full items-center justify-center gap-2 rounded-2xl border border-[#10b981]/40 bg-[#10b981]/15 text-base font-semibold text-[#a7f3d0] shadow-[0_8px_24px_rgba(16,185,129,0.15)] transition-all duration-200 hover:border-[#10b981]/60 hover:bg-[#10b981]/25 hover:shadow-[0_12px_32px_rgba(16,185,129,0.25)] disabled:pointer-events-none disabled:opacity-40"
            >
              <span>Start Game</span>
            </button>
          </aside>
        }
        boardPanel={
          <div className="relative mx-auto aspect-square w-full max-w-[calc(100vh-70px)]">
            <div
              className="h-full w-full"
              style={{
                padding: '14px',
                ...woodBoxStyle,
                borderRadius: '6px',
                boxShadow:
                  '0 0 0 2px #1a0a02, inset 0 2px 0 rgba(255,200,100,0.12), inset 0 -2px 0 rgba(0,0,0,0.5), 0 12px 40px rgba(0,0,0,0.6)',
              }}
            >
              <div className="relative h-full w-full">
                <div
                  style={{
                    position: 'absolute',
                    inset: 0,
                    backgroundImage: 'url("/wood-texture.webp")',
                    backgroundSize: 'cover',
                    opacity: 0.08,
                    pointerEvents: 'none',
                    mixBlendMode: 'multiply' as React.CSSProperties['mixBlendMode'],
                  }}
                />
                <Chessboard
                  options={{
                    // Unique DOM id: the library measures squares with
                    // document.querySelector, so co-mounted boards must
                    // never share the default 'chessboard' id (a modal
                    // board would otherwise animate with the main
                    // board's square size and overshoot its target).
                    id: 'sparring-board',
                    position: viewFen,
                    boardOrientation: humanColor,
                    allowDragging: humanCanMove || canPremove,
                    canDragPiece: ({ piece }) => {
                      if (humanCanMove) {
                        return piece.pieceType[0] === viewTurn;
                      }
                      if (!canPremove) {
                        return false;
                      }
                      return piece.pieceType[0] === humanPieceColor;
                    },
                    onPieceDrop: handleDrop,
                    onSquareClick: handleSquareClick,
                    onSquareRightClick: handleSquareRightClick,
                    squareRenderer,
                    boardStyle: {
                      width: '100%',
                      height: '100%',
                      borderRadius: '8px',
                      boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
                    },
                    darkSquareStyle: {
                      backgroundImage: 'url(/walnut-dark.webp)',
                      backgroundSize: '110% 110%',
                      backgroundPosition: 'center',
                    },
                    lightSquareStyle: {
                      backgroundImage: 'url(/walnut-light.webp)',
                      backgroundSize: '110% 110%',
                      backgroundPosition: 'center',
                    },
                    darkSquareNotationStyle: { color: '#f0e0c0' },
                    lightSquareNotationStyle: { color: '#3a2410' },
                    animationDurationInMs: 200,
                  }}
                />
              </div>
            </div>
          </div>
        }
        analysisPanel={
          <aside className={rightPanelClass}>
            {isStarted ? (
              <>
                {/* Moves fill the card: no nested boxes, one uniform surface. */}
                <div className="flex min-h-0 flex-1 flex-col overflow-hidden px-4 pt-4">
                  <div className="flex shrink-0 items-center justify-between gap-2">
                    <span className="text-[11px] font-bold uppercase tracking-[0.3em] text-[#f7e5c6]/60">
                      Moves
                    </span>
                  </div>

                  <div
                    ref={moveListRef}
                    className="wood-scrollbar mt-2 min-h-0 flex-1 overflow-y-auto pb-2"
                  >
                    {history.length === 0 ? (
                      <p className="px-1 py-3 text-[12px] leading-5 text-white/40">
                        {humanColor === 'white'
                          ? 'No moves yet - play your first move on the board.'
                          : 'The engine opens while you watch - moves appear here.'}
                      </p>
                    ) : (
                      <div className="flex flex-col gap-0.5">
                        {moveRows.map((row) => (
                          <div key={row.startPly} className="flex items-center gap-1">
                            <button
                              type="button"
                              onClick={() => goToIndex(row.startPly)}
                              className="w-7 shrink-0 rounded-md px-1 py-1 text-right text-[11px] font-semibold text-white/35 transition hover:text-white/60"
                              aria-label={`Go to position before move ${row.number}`}
                            >
                              {row.number}.
                            </button>
                            <button
                              type="button"
                              onClick={() => goToIndex(row.startPly + 1)}
                              className={moveCellClass(safeViewIndex === row.startPly + 1)}
                            >
                              {row.white}
                            </button>
                            {row.black ? (
                              <button
                                type="button"
                                onClick={() => goToIndex(row.startPly + 2)}
                                className={moveCellClass(safeViewIndex === row.startPly + 2)}
                              >
                                {row.black}
                              </button>
                            ) : (
                              <span className="flex-1" />
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>

                {/* Resign / Hint / Undo on the same surface, divided by hairlines. */}
                <div className="shrink-0 border-t border-black/40 px-4 py-3">
                  <div className="grid grid-cols-3 gap-3">
                    <button
                      type="button"
                      onClick={resignGame}
                      disabled={over}
                      className={DANGER_BUTTON_CLASS}
                    >
                      <FlagIcon />
                      <span>Resign</span>
                    </button>
                    <button
                      type="button"
                      onClick={requestHint}
                      disabled={!humanCanMove || isHintLoading}
                      className={GOLD_BUTTON_CLASS}
                      title={
                        hint ? 'Play the hinted move' : 'Highlight the piece to move'
                      }
                    >
                      {isHintLoading ? (
                        <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-[#2a1a06]/40 border-t-[#2a1a06]" />
                      ) : hint ? (
                        <EyeIcon />
                      ) : (
                        <BulbIcon />
                      )}
                      <span>{hint ? 'Solve' : 'Hint'}</span>
                    </button>
                    <button
                      type="button"
                      onClick={undoMove}
                      disabled={history.length === 0 || isThinking || over}
                      className={SECONDARY_BUTTON_CLASS}
                    >
                      <UndoIcon />
                      <span>Undo</span>
                    </button>
                  </div>
                </div>
              </>
            ) : (
              <div className="wood-scrollbar flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4">
                <WeakOpenings
                  openings={selectedProfile?.openings_lost_against ?? []}
                  warnings={importWarnings}
                  provider={selectedProfile?.provider ?? null}
                  username={selectedProfile?.opponent_username ?? null}
                />

                <RecurringBlunders key={trapsPanelKey} profile={selectedProfile} />

                <PreferredTimeControl
                  distribution={selectedProfile?.time_control_distribution ?? null}
                  mostPlayed={selectedProfile?.preferred_time_control ?? null}
                />
              </div>
            )}
          </aside>
        }
      />

      {showResult && over && isStarted && (
        <GameOverModal
          outcome={resultOutcome}
          moves={Math.ceil(history.length / 2)}
          hintsUsed={hintsUsed}
          opponentRating={selectedProfile?.rating ?? null}
          onRematch={rematch}
          onExitPrep={exitToPrep}
          onClose={() => setShowResult(false)}
        />
      )}
    </div>
  );
}

// The end-of-game card: who won and why, a small game summary, and the two
// follow-ups (back to the prep overview or a rematch). Same modal as the
// engine sparring page; dismissable so the final position and move list
// stay reviewable.
function GameOverModal({
  outcome,
  moves,
  hintsUsed,
  opponentRating,
  onRematch,
  onExitPrep,
  onClose,
}: {
  outcome: GameOutcome;
  moves: number;
  hintsUsed: number;
  opponentRating: number | null;
  onRematch: () => void;
  onExitPrep: () => void;
  onClose: () => void;
}) {
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose();
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 px-4 backdrop-blur-sm"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={`${outcome.title} ${outcome.subtitle}`}
    >
      <div
        className={`${MODAL_CARD_CLASS} relative w-full max-w-md rounded-2xl p-6`}
        onClick={(event) => event.stopPropagation()}
      >
        <button
          type="button"
          onClick={onClose}
          aria-label="Close and review the game"
          className="absolute right-3 top-3 flex h-8 w-8 items-center justify-center rounded-lg text-[#f7e5c6]/70 transition hover:bg-white/10 hover:text-[#f7e5c6]"
        >
          <CloseIcon />
        </button>

        <h2 className="mt-2 text-center font-display text-2xl font-bold text-[#efd9a7]">
          {outcome.title}
        </h2>
        <p className="mt-1 text-center text-sm text-[#a79b8a]">
          {outcome.subtitle}
        </p>

        <div className="mt-6 grid grid-cols-3 gap-3">
          <ResultStat icon={<MovesIcon />} value={moves} label="Moves" />
          <ResultStat icon={<BulbIcon />} value={hintsUsed} label="Hints" />
          <ResultStat
            icon={<RatingIcon />}
            value={opponentRating ?? '–'}
            label="Opp. Elo"
          />
        </div>

        <div className="mt-6 grid grid-cols-2 gap-3">
          <button
            type="button"
            onClick={onExitPrep}
            className={MODAL_SECONDARY_BUTTON_CLASS}
          >
            Back to Prep
          </button>
          <button
            type="button"
            onClick={onRematch}
            className={MODAL_PRIMARY_BUTTON_CLASS}
          >
            Rematch
          </button>
        </div>
      </div>
    </div>
  );
}

function ResultStat({
  icon,
  value,
  label,
}: {
  icon: React.ReactNode;
  value: React.ReactNode;
  label: string;
}) {
  return (
    <div className="flex flex-col items-center gap-2">
      <div className="flex items-center gap-2">
        <span className="flex h-7 w-7 items-center justify-center rounded-full bg-[#d9b87c]/15 text-[#eacb90]">
          {icon}
        </span>
        <span className="font-display text-lg font-bold tabular-nums text-[#f7e5c6]">
          {value}
        </span>
      </div>
      <span className="text-[10px] font-bold uppercase tracking-[0.16em] text-[#f7e5c6]/45">
        {label}
      </span>
    </div>
  );
}

// The result modal's headline + reason: "<opponent> Won / by resignation"
// mirrors the sparring page's "Nora Won / by resignation" shape.
function describeOutcome(
  game: Chess,
  resigned: boolean,
  playerColor: 'white' | 'black',
  opponentName: string
): GameOutcome {
  if (resigned) {
    return { title: `${opponentName} Won`, subtitle: 'by resignation' };
  }
  if (game.isCheckmate()) {
    const winner = game.turn() === 'w' ? 'black' : 'white';
    return {
      title: winner === playerColor ? 'You Won' : `${opponentName} Won`,
      subtitle: 'by checkmate',
    };
  }
  if (game.isStalemate()) {
    return { title: 'Draw', subtitle: 'by stalemate' };
  }
  if (game.isInsufficientMaterial()) {
    return { title: 'Draw', subtitle: 'insufficient material' };
  }
  if (game.isThreefoldRepetition()) {
    return { title: 'Draw', subtitle: 'by repetition' };
  }
  if (game.isDraw()) {
    return { title: 'Draw', subtitle: 'by the fifty-move rule' };
  }
  return { title: 'Game Over', subtitle: '' };
}

function ProfileCard({
  profile,
  loadError,
}: {
  profile: OpponentProfile | null;
  loadError: string | null;
}) {
  const displayName = profile?.opponent_username ?? 'No opponent';
  const initials = (profile?.opponent_username ?? '-')
    .replace(/[^A-Za-z0-9]/g, '')
    .slice(0, 2)
    .toUpperCase();
  const showVerified = profile?.verified ?? false;
  const avatarUrl = profile?.avatar_url ?? null;

  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-center gap-3">
        <Avatar avatarUrl={avatarUrl} initials={initials} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <h2 className="truncate text-base font-semibold text-[#f7e5c6]">
              {displayName}
            </h2>
            {showVerified && <VerifiedBadge />}
          </div>
        </div>
      </div>

      {loadError && (
        <p role="alert" className="rounded-2xl border border-red-400/30 bg-red-400/10 px-3 py-2 text-xs text-red-300">
          {loadError}
        </p>
      )}
    </section>
  );
}

function Avatar({ avatarUrl, initials }: { avatarUrl: string | null; initials: string }) {
  return (
    <div className="relative h-14 w-14 shrink-0 overflow-hidden rounded-full border-2 border-black/50 bg-black/40 transition-shadow duration-200 hover:ring-2 hover:ring-emerald-400/30">
      {avatarUrl ? (
        // eslint-disable-next-line @next/next/no-img-element -- chess.com avatars come from arbitrary CDN hostnames
        <img
          src={avatarUrl}
          alt=""
          className="h-full w-full object-cover"
          loading="lazy"
        />
      ) : (
        <div className="flex h-full w-full items-center justify-center bg-gradient-to-br from-amber-700/30 to-black text-base font-semibold text-[#f7e5c6]/85">
          {initials}
        </div>
      )}
    </div>
  );
}

function VerifiedBadge() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="#10b981"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-label="Verified"
      role="img"
    >
      <path d="m5 12 4 4L19 6" fill="#10b981" />
    </svg>
  );
}

function RatingsRow({
  ratings,
}: {
  ratings: Partial<Record<TimeClassKey, number>> | null;
}) {
  const entries = TIME_CLASS_ORDER
    .map((key) => {
      const value = ratings?.[key];
      if (value === undefined || value === null) return null;
      return { key, value, meta: TIME_CLASS_META[key] };
    })
    .filter((entry): entry is { key: TimeClassKey; value: number; meta: (typeof TIME_CLASS_META)[TimeClassKey] } => entry !== null);

  return (
    <section className="grid grid-cols-3 gap-2">
      {entries.length === 0 ? (
        <div className="col-span-3 h-16 rounded-2xl border border-black/30 bg-black/25" aria-hidden />
      ) : (
        entries.map(({ key, value, meta }) => (
          <div
            key={key}
            className="flex cursor-default flex-col items-center gap-1 rounded-2xl border border-black/30 bg-black/30 px-2 py-2.5 transition-colors duration-200 hover:border-emerald-400/30 hover:bg-emerald-400/[0.06]"
          >
            <div className={meta.tone}>{meta.icon('h-8 w-8')}</div>
            <span className="sr-only">{meta.label}</span>
            <div className="text-lg font-semibold leading-none text-[#f7e5c6]">
              {value}
            </div>
          </div>
        ))
      )}
    </section>
  );
}

function PlayingStylePill({
  style,
}: {
  style: 'Passive' | 'Balanced' | 'Aggressive' | null;
}) {
  return (
    <section className="flex items-center justify-between gap-3 rounded-2xl border border-black/30 bg-black/25 px-3 py-2.5">
      <span className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[#f7e5c6]/55">
        Playing Style
      </span>
      {style ? (
        <span
          className={`inline-flex items-center rounded-full border px-3 py-1 text-xs font-semibold uppercase tracking-wider ${STYLE_PILL[style].ring} ${STYLE_PILL[style].bg} ${STYLE_PILL[style].text}`}
        >
          {style}
        </span>
      ) : (
        <span className="text-xs text-[#f7e5c6]/40">-</span>
      )}
    </section>
  );
}

function TimeClassSelect({
  value,
  onChange,
  disabled,
}: {
  value: TimeClassKey | '';
  onChange: (next: TimeClassKey) => void;
  disabled: boolean;
}) {
  return (
    <section className="flex flex-col gap-1.5">
      <span className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[#f7e5c6]/55">
        Time Control
      </span>
      <div
        role="group"
        aria-label="Time control"
        className="grid grid-cols-3 gap-1 rounded-2xl border border-black/40 bg-black/35 p-1"
      >
        {TIME_CLASS_ORDER.map((key) => {
          const meta = TIME_CLASS_META[key];
          const isSelected = value === key;
          return (
            <button
              key={key}
              type="button"
              onClick={() => onChange(key)}
              disabled={disabled}
              aria-pressed={isSelected}
              className={`flex h-10 items-center justify-center gap-1.5 rounded-xl border border-transparent text-xs font-semibold transition-all duration-200 disabled:pointer-events-none disabled:opacity-50 ${
                isSelected
                  ? (meta.selectedClass ??
                    'bg-[#f7e5c6] text-[#20120a] shadow-[0_8px_22px_rgba(0,0,0,0.3)]')
                  : 'text-[#f7e5c6]/65 hover:bg-white/[0.06] hover:text-[#f7e5c6]'
              }`}
            >
              {(meta.controlIcon ?? meta.icon)('h-4 w-4')}
              <span>{meta.label}</span>
            </button>
          );
        })}
      </div>
    </section>
  );
}

function PlayAsSelect({
  value,
  onChange,
  disabled,
}: {
  value: 'white' | 'black';
  onChange: (next: 'white' | 'black') => void;
  disabled: boolean;
}) {
  const options: { value: 'white' | 'black'; label: string; icon: string }[] = [
    { value: 'white', label: 'White', icon: '♔' },
    { value: 'black', label: 'Black', icon: '♚' },
  ];

  return (
    <section className="flex flex-col gap-1.5">
      <label className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[#f7e5c6]/55">
        Play As
      </label>
      <div className="grid grid-cols-2 gap-2 rounded-2xl border border-black/40 bg-black/35 p-1">
        {options.map((option) => {
          const isSelected = value === option.value;
          return (
            <button
              key={option.value}
              type="button"
              onClick={() => onChange(option.value)}
              disabled={disabled}
              className={`flex h-10 items-center justify-center gap-2 rounded-xl text-sm font-semibold transition-all duration-200 disabled:pointer-events-none disabled:opacity-50 ${
                isSelected
                  ? 'bg-[#f7e5c6] text-[#20120a] shadow-[0_8px_22px_rgba(0,0,0,0.3)]'
                  : 'text-[#f7e5c6]/65 hover:bg-white/[0.06] hover:text-[#f7e5c6]'
              }`}
              aria-pressed={isSelected}
            >
              <span className="text-base leading-none" aria-hidden>
                {option.icon}
              </span>
              <span>{option.label}</span>
            </button>
          );
        })}
      </div>
    </section>
  );
}

function WeakOpenings({
  openings,
  warnings,
  provider,
  username,
}: {
  openings: OpponentProfile['openings_lost_against'];
  warnings: string[];
  provider: 'lichess' | 'chesscom' | null;
  username: string | null;
}) {
  // Bucket currently open in the replay modal (null = modal closed).
  const [selectedOpening, setSelectedOpening] = useState<{
    family: string;
    color: 'white' | 'black';
  } | null>(null);

  // The API already sorts by shrunk loss rate; re-sort defensively in case
  // a caller ever hand-builds the array.
  const top = [...openings]
    .sort((a, b) => b.loss_rate - a.loss_rate)
    .slice(0, 5);

  const renderOpening = (
    opening: (typeof top)[number],
    index: number,
    featured = false
  ) => {
    const percentage = Math.round(opening.loss_rate * 100);
    const wins = opening.raw_wins ?? 0;
    const losses = opening.raw_losses ?? 0;
    const draws = opening.raw_draws ?? 0;
    const hasCounts =
      opening.raw_wins !== null &&
      opening.raw_losses !== null &&
      opening.raw_draws !== null;
    const canReview = Boolean(opening.color && provider && username);

    return (
      <button
        key={opening.name}
        type="button"
        onClick={() => {
          if (opening.color && provider && username) {
            setSelectedOpening({ family: opening.family, color: opening.color });
          }
        }}
        disabled={!canReview}
        title={canReview ? 'Review a game from this opening' : undefined}
        aria-label={`${canReview ? 'Review' : 'Opening'} ${opening.family}${opening.color ? ` as ${opening.color}` : ''}, ${percentage}% loss rate`}
        className={`group relative block w-full overflow-hidden rounded-2xl border text-left transition duration-200 enabled:cursor-pointer focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#efd9a7] disabled:cursor-default ${
          featured
            ? 'border-rose-300/20 bg-[linear-gradient(115deg,rgba(127,29,29,0.24),rgba(0,0,0,0.42))] p-3.5 shadow-[inset_0_1px_0_rgba(255,255,255,0.05)] hover:border-rose-200/35 hover:bg-[linear-gradient(115deg,rgba(127,29,29,0.31),rgba(0,0,0,0.45))] disabled:hover:border-rose-300/20'
            : 'border-[#f7e5c6]/[0.08] bg-black/25 px-3 py-2.5 hover:border-rose-300/20 hover:bg-rose-950/20 disabled:hover:border-[#f7e5c6]/[0.08] disabled:hover:bg-black/25'
        }`}
      >
        {featured ? (
          <>
            <div className="flex items-center justify-between gap-3">
              <span className="inline-flex items-center gap-1.5 text-[9px] font-bold uppercase tracking-[0.18em] text-rose-200/80">
                <span className="h-1.5 w-1.5 rounded-full bg-rose-300 shadow-[0_0_10px_rgba(251,113,133,0.7)]" aria-hidden />
                Biggest weakness
              </span>
            </div>

            <div className="mt-2 flex items-start justify-between gap-3">
              <div className="flex min-w-0 items-start gap-2.5">
                <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-rose-200/15 bg-rose-300/10 text-[10px] font-bold tabular-nums text-rose-100/90">
                  01
                </span>
                <div className="min-w-0">
                  <div className="truncate text-[15px] font-semibold text-[#fff2df]">
                    {opening.family}
                  </div>
                  {opening.color && (
                    <div className="mt-0.5 text-[11px] text-[#f7e5c6]/55">
                      Playing as {opening.color === 'white' ? 'White' : 'Black'}
                    </div>
                  )}
                </div>
              </div>
              <div className="shrink-0 text-right">
                <div className="text-[22px] font-semibold leading-none tabular-nums text-rose-200">
                  {percentage}%
                </div>
                <div className="mt-1 text-[9px] font-semibold uppercase tracking-[0.15em] text-[#f7e5c6]/40">
                  loss rate
                </div>
              </div>
            </div>

            <div className="mt-3 h-2 overflow-hidden rounded-full bg-black/45 ring-1 ring-inset ring-white/[0.04]">
              <div
                className="h-full rounded-full bg-gradient-to-r from-rose-600 to-amber-300 transition-[width] duration-500 ease-out"
                style={{ width: `${Math.max(3, Math.min(100, percentage))}%` }}
              />
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] text-[#f7e5c6]/55">
              {hasCounts ? (
                <>
                  <span className="tabular-nums">
                    <span className="text-emerald-200/80">{wins}W</span>{' '}
                    <span className="text-rose-200/90">{losses}L</span>{' '}
                    <span>{draws}D</span>
                  </span>
                </>
              ) : (
                <span>Weighted estimate · re-import to refresh</span>
              )}
              {opening.low_sample && (
                <span className="rounded-full border border-amber-300/25 bg-amber-300/[0.08] px-2 py-0.5 text-[9px] font-semibold uppercase tracking-[0.12em] text-amber-200">
                  Low sample
                </span>
              )}
            </div>
          </>
        ) : (
          <>
            <div className="flex items-center justify-between gap-3">
              <div className="flex min-w-0 items-center gap-2.5">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-lg border border-[#f7e5c6]/10 bg-white/[0.04] text-[9px] font-bold tabular-nums text-[#f7e5c6]/55">
                  {String(index + 1).padStart(2, '0')}
                </span>
                <div className="min-w-0">
                  <div className="truncate text-xs font-semibold text-[#f7e5c6]/90">
                    {opening.family}
                    {opening.color && (
                      <span className="ml-1.5 text-[10px] font-normal text-[#f7e5c6]/45">
                        as {opening.color === 'white' ? 'White' : 'Black'}
                      </span>
                    )}
                  </div>
                </div>
              </div>
              <span className="shrink-0 text-right text-xs font-semibold tabular-nums text-rose-200/90">
                {percentage}%
                <span className="ml-1 text-[9px] font-medium uppercase tracking-wider text-[#f7e5c6]/35">loss</span>
              </span>
            </div>
            <div className="ml-[34px] mt-1.5 flex items-center gap-2.5">
              <div className="h-1 flex-1 overflow-hidden rounded-full bg-black/45">
                <div
                  className="h-full rounded-full bg-gradient-to-r from-rose-700 to-amber-300/90 transition-[width] duration-500 ease-out"
                  style={{ width: `${Math.max(3, Math.min(100, percentage))}%` }}
                />
              </div>
              <div className="shrink-0 text-[9px] tabular-nums text-[#f7e5c6]/45">
                {hasCounts
                  ? `${wins}W · ${losses}L · ${draws}D`
                  : 'weighted estimate · re-import to refresh'}
                {opening.low_sample && <span className="ml-1.5 text-amber-200/80">· low sample</span>}
              </div>
            </div>
          </>
        )}
      </button>
    );
  };

  return (
    <section className="rounded-[18px] border border-[#f7e5c6]/10 bg-black/25 p-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]">
      <div className="flex items-center justify-between gap-3 border-b border-white/5 pb-2">
        <div className="flex min-w-0 items-center gap-2">
          <OpeningWeaknessReliefIcon />
          <div className="min-w-0">
            <h3 className="text-[11px] font-semibold uppercase tracking-[0.2em] text-[#f7e5c6]">
              Weak Openings
            </h3>
          </div>
        </div>
      </div>

      {warnings.length > 0 && (
        <div
          role="alert"
          className="mt-2 rounded-xl border border-amber-400/30 bg-amber-400/10 px-3 py-2 text-[11px] leading-relaxed text-amber-200"
        >
          {warnings.map((warning) => (
            <p key={warning}>{warning}</p>
          ))}
        </div>
      )}

      {top.length === 0 ? (
        <div className="mt-3">
          <EmptyHint text="No decisive opening data yet." />
        </div>
      ) : (
        <div className="mt-3 flex flex-col gap-2">
          {renderOpening(top[0], 0, true)}
          {top.length > 1 && (
            <div className="flex flex-col gap-1.5">
              {top.slice(1).map((opening, index) => renderOpening(opening, index + 1))}
            </div>
          )}
        </div>
      )}

      {selectedOpening && provider && username && (
        <OpeningReplayModal
          provider={provider}
          username={username}
          family={selectedOpening.family}
          color={selectedOpening.color}
          onClose={() => setSelectedOpening(null)}
        />
      )}
    </section>
  );
}

function RecurringBlunders({ profile }: { profile: OpponentProfile | null }) {
  const provider = profile?.provider ?? null;
  const username = profile?.opponent_username ?? null;
  // The parent keys this panel by opponent, so switching opponents
  // remounts it and the state below starts from the new profile - no
  // set-state-in-effect reset.
  const [traps, setTraps] = useState<OpponentTrap[]>(profile?.traps ?? []);
  const [phase, setPhase] = useState<TrapsPhase>(
    provider && username ? 'checking' : 'idle'
  );
  const [progress, setProgress] = useState<{ analyzed: number; total: number } | null>(null);
  // Why polling stopped with an error (null while healthy). 'not-started'
  // = the job row never appeared (import trigger died silently);
  // 'stalled' = a running job whose heartbeat went stale (worker crashed).
  const [failReason, setFailReason] = useState<'not-started' | 'stalled' | null>(null);
  // Trap row currently open in the position viewer (null = modal closed).
  const [selectedTrap, setSelectedTrap] = useState<OpponentTrap | null>(null);

  // Poll the blunder-analysis job for THIS opponent only, and refresh the
  // traps in panel-local state. Openings/Time Control stay untouched while
  // the Stockfish pass grinds through hundreds of games.
  useEffect(() => {
    if (!provider || !username) {
      return;
    }
    const providerKey = provider;
    const usernameKey = username;

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    // Whether any status row was ever observed this mount. Distinguishes
    // "analysis never started" (trigger died before creating the row) from
    // transient 404s after a row was already seen.
    let sawRow = false;

    function heartbeatStale(heartbeatAt: string | null): boolean {
      if (!heartbeatAt) {
        return false;
      }
      const parsed = Date.parse(heartbeatAt);
      if (Number.isNaN(parsed)) {
        return false;
      }
      return Date.now() - parsed > ANALYSIS_HEARTBEAT_STALE_MS;
    }

    function fail(reason: 'not-started' | 'stalled') {
      if (!cancelled) {
        setFailReason(reason);
        setPhase('failed');
      }
    }

    async function refreshTraps() {
      try {
        const response = await fetch('/api/train/opponents', { cache: 'no-store' });
        if (!response.ok) {
          return;
        }
        const data = (await response.json()) as { opponents: OpponentProfile[] };
        if (cancelled) {
          return;
        }
        const match = data.opponents.find(
          (opponent) =>
            opponent.provider === providerKey &&
            opponent.opponent_username.toLowerCase() === usernameKey.toLowerCase()
        );
        if (match) {
          setTraps(match.traps ?? []);
        }
      } catch {
        // Keep the traps already on screen; the refresh is best-effort.
      }
    }

    // The job row may not exist yet on the first attempts (see
    // ANALYSIS_JOB_WAIT_ATTEMPTS); waitAttempt counts consecutive misses.
    const keepWaiting = (waitAttempt: number) =>
      !cancelled && waitAttempt < ANALYSIS_JOB_WAIT_ATTEMPTS;

    async function pollAnalysis(waitAttempt = 0) {
      try {
        const response = await fetch(
          `/api/train/opponent-analysis?provider=${providerKey}&opponent_username=${encodeURIComponent(usernameKey)}`,
          { cache: 'no-store' }
        );
        if (!response.ok) {
          if (keepWaiting(waitAttempt)) {
            timer = setTimeout(
              () => pollAnalysis(waitAttempt + 1),
              ANALYSIS_JOB_WAIT_INTERVAL_MS
            );
            return;
          }
          // Wait window exhausted. A row seen earlier means the job
          // existed (transient read failure - stay idle and keep showing
          // whatever traps are on screen). No row ever seen means the
          // import trigger died before creating one: say so instead of
          // the generic "no traps" copy.
          if (!cancelled) {
            if (sawRow) {
              setPhase('idle');
            } else {
              fail('not-started');
            }
          }
          return;
        }
        const data = (await response.json()) as AnalysisStatusResponse;
        if (cancelled) {
          return;
        }
        sawRow = true;
        // No job / no games / unanalyzable corpus: fall back to the plain
        // empty state instead of spinning forever.
        if (data.total_games <= 0) {
          setPhase('idle');
          return;
        }
        // Status is authoritative when counts are stale (e.g. a job row
        // from before the recency cap, or a worker killed mid-run and then
        // normalized to complete by the next trigger).
        const analysisComplete =
          data.status === 'complete' ||
          data.analyzed_games >= data.total_games;
        // A running job with a stale heartbeat has no live worker (the
        // backend reclaims after 5 min) - e.g. Stockfish crashed mid-run.
        // Stop polling and say so; a re-import reclaims and restarts it.
        if (!analysisComplete && heartbeatStale(data.heartbeat_at)) {
          fail('stalled');
          return;
        }
        if (!analysisComplete) {
          setProgress({ analyzed: data.analyzed_games, total: data.total_games });
          setPhase('polling');
          timer = setTimeout(pollAnalysis, ANALYSIS_POLL_INTERVAL_MS);
          return;
        }
        // Analysis finished - pull the traps it produced, then stop.
        setProgress({ analyzed: data.analyzed_games, total: data.total_games });
        await refreshTraps();
        if (!cancelled) {
          setPhase('complete');
        }
      } catch {
        if (keepWaiting(waitAttempt)) {
          timer = setTimeout(
            () => pollAnalysis(waitAttempt + 1),
            ANALYSIS_JOB_WAIT_INTERVAL_MS
          );
          return;
        }
        if (!cancelled) {
          setPhase('idle');
        }
      }
    }

    pollAnalysis();

    return () => {
      cancelled = true;
      if (timer !== null) {
        clearTimeout(timer);
      }
    };
  }, [provider, username]);

  const top = traps.slice(0, 5);
  const isAnalyzing = phase === 'polling';

  return (
    <section className="rounded-[18px] border border-[#f7e5c6]/10 bg-black/25 p-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]">
      <SectionHeader icon={<KnightReliefIcon size="sm" />} title="Recurring Blunders" />

      {top.length > 0 && isAnalyzing && (
        <div className="mt-2 flex items-center gap-2 text-[11px] text-[#f7e5c6]/55">
          <span className="h-2 w-2 animate-pulse rounded-full bg-emerald-400/80" aria-hidden />
          Analyzing {progress?.analyzed ?? 0}/{progress?.total ?? 0} games…
        </div>
      )}

      {top.length > 0 && phase === 'failed' && (
        <p className="mt-2 text-[11px] leading-5 text-rose-200/80">
          Analysis stopped early - these traps may be partial. Try
          re-importing to restart it.
        </p>
      )}

      {top.length === 0 ? (
        <div className="mt-3">
          {phase === 'failed' ? (
            <div className="rounded-2xl border border-rose-400/30 bg-rose-500/10 px-3 py-2.5 text-[11px] leading-5 text-rose-200">
              {failReason === 'not-started'
                ? "Analysis couldn't start - try re-importing the opponent."
                : 'Analysis stalled - try re-importing to restart it.'}
            </div>
          ) : phase === 'checking' || phase === 'polling' ? (
            <div className="flex items-center gap-2 rounded-2xl border border-black/30 bg-black/30 px-3 py-2.5 text-[11px] text-[#f7e5c6]/60">
              <span className="h-2 w-2 animate-pulse rounded-full bg-emerald-400/80" aria-hidden />
              {phase === 'polling'
                ? `Analyzing ${progress?.analyzed ?? 0}/${progress?.total ?? 0} games…`
                : 'Checking analysis progress…'}
            </div>
          ) : (
            <EmptyHint
              text={
                phase === 'complete'
                  ? (profile?.game_count ?? 0) < 5
                    ? 'Not enough games yet - traps need 5+ games on file.'
                    : 'Analysis complete - no recurring patterns found.'
                  : 'No recurring traps detected yet.'
              }
            />
          )}
        </div>
      ) : (
        <div className="mt-3 flex flex-col gap-2">
          {top.map((trap) => {
            const moveLabel = trap.moves.length > 0 ? trap.moves[0] : '?';
            const moveRange =
              trap.move_number_min === trap.move_number_max
                ? `move ${trap.move_number_min}`
                : `moves ${trap.move_number_min}-${trap.move_number_max}`;
            const games = `${trap.game_count} game${trap.game_count === 1 ? '' : 's'}`;
            const classificationTone =
              trap.classification === 'blunder'
                ? 'border-rose-400/30 bg-rose-500/10 text-rose-200'
                : 'border-amber-400/30 bg-amber-500/10 text-amber-200';
            const accent =
              trap.classification === 'blunder' ? 'bg-rose-400' : 'bg-amber-300';

            return (
              <button
                key={trap.position_key}
                type="button"
                onClick={() => setSelectedTrap(trap)}
                title="View position"
                className="group relative block w-full cursor-pointer overflow-hidden rounded-2xl border border-black/30 bg-black/30 px-3 py-2.5 text-left transition-colors duration-200 hover:border-emerald-400/25 hover:bg-emerald-400/[0.05] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#efd9a7]"
              >
                <div className={`absolute inset-y-3 left-0 w-1 rounded-r-full ${accent}`} />
                <div className="flex items-start justify-between gap-3 pl-1.5">
                  <div className="min-w-0 flex-1">
                    <div className="flex min-w-0 items-center gap-2">
                      <span className="truncate text-sm font-semibold text-[#f7e5c6]">
                        Played {moveLabel}
                      </span>
                      <span
                        className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.14em] ${classificationTone}`}
                      >
                        {trap.classification}
                      </span>
                      {trap.exploitable ? (
                        <span
                          title="Clears the bot's bar - sparring steers toward this position"
                          className="shrink-0 rounded-full border border-emerald-400/30 bg-emerald-500/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-emerald-200"
                        >
                          In bot&apos;s sights
                        </span>
                      ) : (
                        <span
                          title="Observed pattern below the bot's bar - shown for prep, not played toward"
                          className="shrink-0 rounded-full border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-white/40"
                        >
                          Observed
                        </span>
                      )}
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-[#f7e5c6]/50">
                      <span>{moveRange}</span>
                      <span className="h-1 w-1 rounded-full bg-[#f7e5c6]/25" aria-hidden />
                      <span>{games}</span>
                    </div>
                  </div>
                  <KnightReliefIcon size="md" />
                </div>
              </button>
            );
          })}
        </div>
      )}

      {selectedTrap && (
        <TrapPositionModal
          trap={selectedTrap}
          onClose={() => setSelectedTrap(null)}
        />
      )}
    </section>
  );
}

// Shared modal shell: wood card, Escape/overlay close, dialog semantics.
// Reused by the trap position viewer now and the opening replay viewer next.
function ModalShell({
  title,
  onClose,
  children,
  hideTitle = false,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
  /** Hides the heading row (title stays as the accessible dialog label)
   *  and floats the close button over the card so the content below keeps
   *  its full height. Used by the weak-opening replay, which must fit
   *  short viewports without scrolling. */
  hideTitle?: boolean;
}) {
  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        onClose();
      }
    }
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex overflow-y-auto bg-black/70 px-4 py-6 backdrop-blur-sm"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div className={modalCardClass} onClick={(event) => event.stopPropagation()}>
        {hideTitle ? (
          <>
            <h2 className="sr-only">{title}</h2>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              className="absolute right-3 top-3 z-10 flex h-8 w-8 items-center justify-center rounded-full border border-white/10 bg-black/65 text-[#f7e5c6]/80 shadow-lg shadow-black/40 transition hover:bg-black/85 hover:text-[#f7e5c6]"
            >
              <svg
                className="h-4 w-4"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth="2"
                aria-hidden="true"
              >
                <path d="M18 6 6 18M6 6l12 12" />
              </svg>
            </button>
          </>
        ) : (
        <div className="flex items-start justify-between gap-3">
          <h2
            className="min-w-0 truncate font-display text-lg font-semibold text-[#f7e5c6]"
            title={title}
          >
            {title}
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-[#f7e5c6]/70 transition hover:bg-white/8 hover:text-[#f7e5c6]"
          >
            <svg
              className="h-4 w-4"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth="2"
              aria-hidden="true"
            >
              <path d="M18 6 6 18M6 6l12 12" />
            </svg>
          </button>
        </div>
        )}
        {children}
      </div>
    </div>
  );
}

// --- Modal explore (shared by both replay viewers) -------------------------
// Opt-in engine layer transplanted from the review sandbox: when armed, the
// studied position gets a live label plus the side-to-move's best reply as
// a board arrow; dragging a different move analyzes the deviation the same
// way. One in-flight request max (new work aborts the previous), stepped
// positions debounce ~500ms so fast click-through never queues the shared
// live engine. Browsing with explore off costs zero engine calls.

type ModalExploreClassification =
  | 'book'
  | 'brilliant'
  | 'great'
  | 'best'
  | 'excellent'
  | 'good'
  | 'inaccuracy'
  | 'mistake'
  | 'miss'
  | 'blunder';

type ModalSandboxLine = {
  move_uci?: string | null;
  move_san?: string | null;
  eval_cp?: number | null;
  eval_mate?: number | null;
  pv_uci: string[];
  pv_san: string[];
};

type ModalStreamMessage =
  | { type: 'meta'; mode: string; depth: number }
  | {
      type: 'info';
      depth: number;
      classification: ModalExploreClassification;
      cp_loss: number;
      ep_loss: number;
      eval_cp: number;
      eval_mate?: number | null;
      best_after?: ModalSandboxLine | null;
    }
  | {
      type: 'final';
      response: {
        classification: ModalExploreClassification;
        cp_loss: number;
        ep_loss: number;
        eval_cp: number;
        eval_mate?: number | null;
        move_san: string;
        fen: string;
        best_after?: ModalSandboxLine | null;
        second_best_after?: ModalSandboxLine | null;
      };
    }
  | { type: 'error'; detail?: string };

type ModalLiveState = {
  key: string | null;
  classification: ModalExploreClassification | null;
  evalCp: number;
  evalMate: number | null;
  suggestionUci: string | null;
  suggestionSan: string | null;
  pending: boolean;
  error: string | null;
};

const EMPTY_MODAL_LIVE: ModalLiveState = {
  key: null,
  classification: null,
  evalCp: 0,
  evalMate: null,
  suggestionUci: null,
  suggestionSan: null,
  pending: false,
  error: null,
};

type VerboseHistoryMove = {
  from: string;
  to: string;
  san: string;
  promotion?: string;
};

// Session-level sandbox capability (the review page fetches its own copy;
// both modals share this one so the second modal costs nothing).
let modalSandboxEnabled: boolean | null = null;

async function modalSandboxAvailable(): Promise<boolean> {
  if (modalSandboxEnabled !== null) {
    return modalSandboxEnabled;
  }
  try {
    const response = await fetch('/api/review/capabilities', {
      cache: 'no-store',
    });
    if (!response.ok) {
      modalSandboxEnabled = false;
      return false;
    }
    const data = (await response.json()) as { sandbox_enabled?: boolean };
    modalSandboxEnabled = data.sandbox_enabled === true;
    return modalSandboxEnabled;
  } catch {
    modalSandboxEnabled = false;
    return false;
  }
}

function formatModalEval(cp: number, mate: number | null): string {
  if (typeof mate === 'number' && Number.isFinite(mate) && mate !== 0) {
    return mate > 0 ? `M${Math.abs(mate)}` : `-M${Math.abs(mate)}`;
  }
  const pawns = (Number.isFinite(cp) ? cp : 0) / 100;
  return `${pawns >= 0 ? '+' : ''}${pawns.toFixed(2)}`;
}

function exploreLabel(classification: ModalExploreClassification): string {
  switch (classification) {
    case 'book':
      return 'Book';
    case 'brilliant':
      return 'Brilliant';
    case 'great':
      return 'Great';
    case 'best':
      return 'Best';
    case 'excellent':
      return 'Excellent';
    case 'good':
      return 'Good';
    case 'inaccuracy':
      return 'Inaccuracy';
    case 'mistake':
      return 'Mistake';
    case 'miss':
      return 'Miss';
    case 'blunder':
      return 'Blunder';
    default:
      return classification;
  }
}

function exploreTone(classification: ModalExploreClassification): string {
  return classification === 'blunder'
    ? 'border-rose-400/30 bg-rose-500/10 text-rose-200'
    : classification === 'mistake' || classification === 'miss'
      ? 'border-amber-400/30 bg-amber-500/10 text-amber-200'
      : classification === 'book'
        ? 'border-white/15 bg-white/5 text-white/70'
        : 'border-emerald-400/30 bg-emerald-500/10 text-emerald-200';
}

function useModalExplore({
  active,
  gameSans,
  gameDetails,
  basePly,
  resetToken,
  defaultArmed = false,
}: {
  /** False while the game PGN is still loading. */
  active: boolean;
  /** SAN history of the replayed game (path context for the sandbox). */
  gameSans: string[];
  /** Verbose history parallel to gameSans (for move UCIs). */
  gameDetails: VerboseHistoryMove[];
  /** Current replay ply (0 = start position). */
  basePly: number;
  /** Clears deviation + live result when the game changes. */
  resetToken: string;
  /** Starts armed. The weak-opening replay passes true (explore always on,
   *  no toggle); the trap viewer leaves it false (opt-in). */
  defaultArmed?: boolean;
}) {
  const [armed, setArmed] = useState(defaultArmed);
  const [live, setLive] = useState<ModalLiveState>(EMPTY_MODAL_LIVE);
  const [deviation, setDeviation] = useState<{
    sans: string[];
    fen: string;
    san: string;
    from: string;
    to: string;
  } | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reqIdRef = useRef(0);

  // New game: drop deviation + live result (armed persists across games).
  useEffect(() => {
    abortRef.current?.abort();
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    setDeviation(null);
    setLive(EMPTY_MODAL_LIVE);
  }, [resetToken]);

  // Abort in-flight work on unmount.
  useEffect(
    () => () => {
      abortRef.current?.abort();
      if (timerRef.current) {
        clearTimeout(timerRef.current);
      }
    },
    []
  );

  const toggleArmed = useCallback(() => {
    abortRef.current?.abort();
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    setLive(EMPTY_MODAL_LIVE);
    setArmed((value) => !value);
  }, []);

  const resetDeviation = useCallback(() => {
    abortRef.current?.abort();
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    setDeviation(null);
    setLive(EMPTY_MODAL_LIVE);
  }, []);

  const runStream = useCallback(
    async (
      pathSans: string[],
      moveUci: string,
      key: string
    ): Promise<{ san: string; fen: string } | null> => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      const reqId = ++reqIdRef.current;
      setLive({ ...EMPTY_MODAL_LIVE, key, pending: true });
      const apply = (partial: Partial<ModalLiveState>) => {
        if (reqIdRef.current === reqId) {
          setLive((prev) =>
            prev.key === key ? { ...prev, ...partial } : prev
          );
        }
      };
      try {
        if (!(await modalSandboxAvailable())) {
          throw new Error('Explore is unavailable right now.');
        }
        const response = await fetch('/api/review/live/stream', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Accept: 'application/x-ndjson',
          },
          body: JSON.stringify({
            moves: pathSans,
            move: moveUci,
            player_rating: null,
            expected_mode: null,
          }),
          signal: controller.signal,
        });
        if (!response.ok) {
          let detail = `Live analysis returned ${response.status}`;
          try {
            const errorBody = (await response.json()) as {
              detail?: unknown;
              error?: unknown;
            };
            const parsed =
              errorBody.detail ?? errorBody.error ?? detail;
            if (typeof parsed === 'string' && parsed.trim()) {
              detail = parsed;
            }
          } catch {
            // Keep the status-based message.
          }
          throw new Error(detail);
        }
        if (!response.body) {
          throw new Error('Live analysis returned no stream');
        }
        const suggestionFrom = (
          lines: Array<ModalSandboxLine | null | undefined>
        ): { uci: string; san: string | null } | null => {
          const line = lines.find((candidate) => candidate?.move_uci);
          if (!line?.move_uci) {
            return null;
          }
          return { uci: line.move_uci, san: line.move_san ?? null };
        };
        let settled: { san: string; fen: string } | null = null;
        const handleMessage = (message: ModalStreamMessage) => {
          if (message.type === 'info') {
            const suggestion = suggestionFrom([message.best_after]);
            apply({
              classification: message.classification,
              evalCp: message.eval_cp,
              evalMate: message.eval_mate ?? null,
              suggestionUci: suggestion?.uci ?? null,
              suggestionSan: suggestion?.san ?? null,
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
          const suggestion = suggestionFrom([
            data.best_after,
            data.second_best_after,
          ]);
          apply({
            pending: false,
            classification: data.classification,
            evalCp: data.eval_cp,
            evalMate: data.eval_mate ?? null,
            suggestionUci: suggestion?.uci ?? null,
            suggestionSan: suggestion?.san ?? null,
          });
          settled = { san: data.move_san, fen: data.fen };
        };
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
              handleMessage(JSON.parse(line) as ModalStreamMessage);
            }
            newline = buffer.indexOf('\n');
          }
        }
        buffer += decoder.decode();
        const tail = buffer.trim();
        if (tail) {
          handleMessage(JSON.parse(tail) as ModalStreamMessage);
        }
        return reqIdRef.current === reqId ? settled : null;
      } catch (error) {
        if (controller.signal.aborted || reqIdRef.current !== reqId) {
          return null;
        }
        const detail =
          error instanceof Error ? error.message : 'Could not analyze that move.';
        setLive((prev) =>
          prev.key === key
            ? { ...prev, pending: false, error: detail }
            : prev
        );
        return null;
      }
    },
    []
  );

  // Auto-analyze the studied game position once stepping settles. Deviations
  // are analyzed at play time, so they are skipped here.
  useEffect(() => {
    if (!armed || !active || deviation) {
      return;
    }
    if (basePly <= 0 || basePly > gameDetails.length) {
      // Start position (or out of range): suggestion only, via prewarm -
      // there is no played move to label.
      if (basePly !== 0) {
        return;
      }
      let cancelled = false;
      timerRef.current = setTimeout(async () => {
        try {
          if (!(await modalSandboxAvailable())) {
            return;
          }
          const response = await fetch('/api/review/live/prewarm', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ moves: [], expected_mode: null }),
          });
          if (!response.ok || cancelled) {
            return;
          }
          const data = (await response.json()) as {
            best?: ModalSandboxLine | null;
            second_best?: ModalSandboxLine | null;
          };
          const line =
            [data.best, data.second_best].find((l) => l?.move_uci) ?? null;
          if (!cancelled && line?.move_uci) {
            setLive({
              ...EMPTY_MODAL_LIVE,
              key: 'start',
              suggestionUci: line.move_uci,
              suggestionSan: line.move_san ?? null,
            });
          }
        } catch {
          // Best-effort; stepping still works without the arrow.
        }
      }, 400);
      return () => {
        cancelled = true;
        if (timerRef.current) {
          clearTimeout(timerRef.current);
          timerRef.current = null;
        }
      };
    }
    const detail = gameDetails[basePly - 1];
    if (!detail) {
      return;
    }
    const uci = `${detail.from}${detail.to}${detail.promotion ?? ''}`;
    const path = gameSans.slice(0, basePly - 1);
    const key = `${path.join(' ')}|${uci}`;
    timerRef.current = setTimeout(() => {
      void runStream(path, uci, key);
    }, 500);
    return () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [armed, active, basePly, gameSans, gameDetails, deviation, runStream]);

  /** Play a deviation from the displayed position (auto-queens promotions).
   *  Returns false when the drag is not a legal move. */
  const playDeviation = useCallback(
    (
      from: string,
      to: string,
      baseFen: string,
      gamePathSans: string[],
      devSans: string[]
    ) => {
      let uci: string | null = null;
      let optimistic: { sans: string[]; fen: string; san: string } | null =
        null;
      try {
        const chess = new Chess(baseFen);
        const options = chess
          .moves({ verbose: true })
          .filter((move) => move.from === from && move.to === to);
        if (options.length === 0) {
          return false;
        }
        const piece = (() => {
          try {
            return chess.get(from as Square);
          } catch {
            return null;
          }
        })();
        const promotion =
          piece?.type === 'p' && (to.charAt(1) === '8' || to.charAt(1) === '1')
            ? 'q'
            : undefined;
        const played = chess.move({ from, to, promotion });
        uci = `${from}${to}${played.promotion ?? ''}`;
        optimistic = {
          sans: [...devSans, played.san],
          fen: chess.fen(),
          san: played.san,
        };
      } catch {
        return false;
      }
      if (!uci || !optimistic) {
        return false;
      }
      const path = [...gamePathSans, ...devSans];
      const key = `${path.join(' ')}|${uci}`;
      // Optimistic: show the dragged move instantly (the position is fully
      // determined locally); the stream below only settles its label and
      // the reply arrow. Without this the piece snaps back until the
      // engine answers seconds later.
      const optimisticSnapshot = optimistic;
      setDeviation({ ...optimisticSnapshot, from, to });
      void runStream(path, uci, key).then((result) => {
        if (result) {
          setDeviation({
            sans: [...devSans, result.san],
            fen: result.fen,
            san: result.san,
            from,
            to,
          });
        }
      });
      return true;
    },
    [runStream]
  );

  const arrows = useMemo(() => {
    if (!armed || !live.suggestionUci || live.suggestionUci.length < 4) {
      return [];
    }
    const from = live.suggestionUci.slice(0, 2);
    const to = live.suggestionUci.slice(2, 4);
    if (from === to) {
      return [];
    }
    return [{ startSquare: from, endSquare: to, color: '#10b981' }];
  }, [armed, live.suggestionUci]);

  return {
    armed,
    toggleArmed,
    live,
    deviation,
    playDeviation,
    resetDeviation,
    arrows,
  };
}

// --- Shared modal viewer chrome -------------------------------------------
// One visual language for both replay viewers: the trap viewer pairs a move
// readout + label chips with copy + explore controls and a wooden
// five-button navigator under the board, while the weak-opening viewer keeps
// explore always on with a single slim status line (no toggle row, no
// heading) so the card fits short viewports without scrolling. Status rows
// keep a minimum height so badges arriving late never shove the board around.

function ModalExploreToggle({
  armed,
  onToggle,
}: {
  armed: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={armed}
      title={
        armed
          ? 'Stop exploring - back to plain replay'
          : 'Explore: label each studied move and show the best reply (drag to try your own moves)'
      }
      className={`shrink-0 cursor-pointer rounded-full border px-3 py-1.5 text-[11px] font-semibold transition-colors duration-200 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#efd9a7] ${
        armed
          ? 'border-emerald-400/50 bg-emerald-500/15 text-emerald-200 hover:bg-emerald-500/25'
          : 'border-[#f7e5c6]/25 bg-black/45 text-[#f7e5c6]/80 hover:bg-black/65'
      }`}
    >
      Explore: {armed ? 'On' : 'Off'}
    </button>
  );
}

function ModalEvalChip({ cp, mate }: { cp: number; mate: number | null }) {
  return (
    <span className="shrink-0 rounded-md bg-[#eacb90]/15 px-2 py-0.5 font-mono text-[11px] font-bold text-[#eacb90] ring-1 ring-[#eacb90]/30">
      {formatModalEval(cp, mate)}
    </span>
  );
}

function ModalLabelChip({
  classification,
  title,
}: {
  classification: ModalExploreClassification;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.14em] ${exploreTone(classification)}`}
    >
      {exploreLabel(classification)}
    </span>
  );
}

function ModalMoveReadout({
  ply,
  total,
  san,
}: {
  ply: number;
  total: number;
  san: string | null;
}) {
  const label =
    ply <= 0 || !san ? 'Start' : `${Math.ceil(ply / 2)}. ${san}`;
  return (
    <span className="flex min-w-0 items-baseline gap-1.5">
      <span className="truncate font-mono text-sm font-bold text-[#f7e5c6]">
        {label}
      </span>
      <span className="shrink-0 font-mono text-[11px] tabular-nums text-[#f7e5c6]/40">
        {ply} / {total}
      </span>
    </span>
  );
}

function ModalCopyFenButton({
  onCopy,
  copied,
}: {
  onCopy: () => void;
  copied: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onCopy}
      title="Copy the displayed FEN"
      className="shrink-0 cursor-pointer rounded-full border border-[#f7e5c6]/25 bg-black/45 px-2.5 py-1.5 text-[10px] font-semibold text-[#f7e5c6]/80 transition-colors duration-200 hover:bg-black/65 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#efd9a7]"
    >
      {copied ? 'Copied' : 'FEN'}
    </button>
  );
}

function ModalWoodNav({
  disabled,
  onFirst,
  onPrev,
  onPlay,
  onNext,
  onLast,
  playing,
}: {
  disabled: boolean;
  onFirst: () => void;
  onPrev: () => void;
  onPlay: () => void;
  onNext: () => void;
  onLast: () => void;
  playing: boolean;
}) {
  const base =
    'flex h-10 flex-1 items-center justify-center text-[#f0e0c0] transition-transform hover:scale-105 active:scale-95 disabled:pointer-events-none disabled:opacity-40 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#efd9a7]';
  const cursor = disabled ? 'default' : 'pointer';
  return (
    <div className="grid shrink-0 grid-cols-5 gap-1.5" role="group" aria-label="Move navigation">
      <button
        type="button"
        onClick={onFirst}
        disabled={disabled}
        aria-label="First position"
        title="First position"
        className={base}
        style={{ cursor, ...woodBoxStyle, borderRadius: '4px 4px 4px 24px' }}
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
          <rect x="3" y="4" width="2.5" height="16" rx="1" />
          <path d="M21 4 L9 12 L21 20 Z" />
        </svg>
      </button>
      <button
        type="button"
        onClick={onPrev}
        disabled={disabled}
        aria-label="Previous move"
        title="Previous move"
        className={base}
        style={{ cursor, ...woodBoxStyle }}
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
          <path d="M18 4 L6 12 L18 20 Z" />
        </svg>
      </button>
      <button
        type="button"
        onClick={onPlay}
        disabled={disabled}
        aria-label={playing ? 'Pause auto-play' : 'Auto-play moves'}
        title={playing ? 'Pause auto-play' : 'Auto-play moves'}
        className={base}
        style={{ cursor, ...woodBoxStyle }}
      >
        {playing ? (
          <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
            <rect x="6" y="5" width="4" height="14" rx="1" />
            <rect x="14" y="5" width="4" height="14" rx="1" />
          </svg>
        ) : (
          <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
            <path d="M8 5v14l11-7z" />
          </svg>
        )}
      </button>
      <button
        type="button"
        onClick={onNext}
        disabled={disabled}
        aria-label="Next move"
        title="Next move"
        className={base}
        style={{ cursor, ...woodBoxStyle }}
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
          <path d="M6 4 L18 12 L6 20 Z" />
        </svg>
      </button>
      <button
        type="button"
        onClick={onLast}
        disabled={disabled}
        aria-label="Last position"
        title="Last position"
        className={base}
        style={{ cursor, ...woodBoxStyle, borderRadius: '4px 4px 24px 4px' }}
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
          <path d="M3 4 L15 12 L3 20 Z" />
          <rect x="18.5" y="4" width="2.5" height="16" rx="1" />
        </svg>
      </button>
    </div>
  );
}

function TrapPositionModal({
  trap,
  onClose,
}: {
  trap: OpponentTrap;
  onClose: () => void;
}) {
  // The stored FEN's side to move is the player who erred here, so orient
  // the board toward them for the whole replay.
  const erringSide: 'white' | 'black' =
    trap.fen.split(' ')[1] === 'b' ? 'black' : 'white';
  // Unique per mount: the trap board co-mounts with the sparring board
  // behind it (see the sparring-board id note above).
  const trapBoardId = `trap-board-${useId().replace(/:/g, '')}`;
  const [phase, setPhase] = useState<'loading' | 'error' | 'ready'>('loading');
  const [positions, setPositions] = useState<string[]>([trap.fen]);
  const [moveIndex, setMoveIndex] = useState(0);
  const [moveSans, setMoveSans] = useState<string[]>([]);
  const [moveDetails, setMoveDetails] = useState<VerboseHistoryMove[]>([]);
  const [blunders, setBlunders] = useState<OpeningGameBlunder[]>([]);
  const [copied, setCopied] = useState(false);

  // Fetch the example game (most recent game with this recurring error) so
  // the user can step back and forward around the position. The static trap
  // FEN stays rendered while loading and if the fetch fails.
  useEffect(() => {
    let cancelled = false;

    async function loadGame() {
      try {
        const response = await fetch(
          `/api/train/opponent-opening-game?game_id=${encodeURIComponent(trap.example_game_id)}`,
          { cache: 'no-store' }
        );
        if (!response.ok) {
          if (!cancelled) {
            setPhase('error');
          }
          return;
        }
        const game = (await response.json()) as OpeningGame;
        if (cancelled) {
          return;
        }
        // Replay locally: positions[i] is the FEN after i plies, so the
        // position before the error (ply p) is positions[p - 1]. Keep the
        // SAN history and verbose details alongside for explore paths.
        const replay = new Chess();
        replay.loadPgn(game.pgn);
        const stepper = new Chess();
        const nextPositions = [START_FEN];
        for (const san of replay.history()) {
          stepper.move(san);
          nextPositions.push(stepper.fen());
        }
        setPositions(nextPositions);
        setMoveSans(replay.history());
        setMoveDetails(
          replay.history({ verbose: true }) as unknown as VerboseHistoryMove[]
        );
        setBlunders(game.blunders ?? []);
        setMoveIndex(Math.max(0, trap.example_ply - 1));
        setPhase('ready');
      } catch {
        if (!cancelled) {
          setPhase('error');
        }
      }
    }

    loadGame();
    return () => {
      cancelled = true;
    };
  }, [trap.example_game_id, trap.example_ply]);

  const lastMoveIndex = positions.length - 1;
  const position = positions[moveIndex] ?? trap.fen;

  const explore = useModalExplore({
    active: phase === 'ready' && positions.length > 1,
    gameSans: moveSans,
    gameDetails: moveDetails,
    basePly: moveIndex,
    resetToken: trap.example_game_id,
  });
  const displayedFen = explore.deviation?.fen ?? position;
  const sideToMove: 'white' | 'black' =
    displayedFen.split(' ')[1] === 'b' ? 'black' : 'white';

  function goToPly(ply: number) {
    explore.resetDeviation();
    setMoveIndex(Math.max(0, Math.min(positions.length - 1, ply)));
  }

  // Stored error badge for the arriving move (free, no engine): match the
  // pre-move position key + SAN against this game's blunder rows.
  const storedBlunder = useMemo(() => {
    if (moveIndex <= 0 || moveIndex > moveDetails.length) {
      return null;
    }
    const key = positionKey(positions[moveIndex - 1] ?? '');
    const san = moveDetails[moveIndex - 1]?.san;
    if (!key || !san) {
      return null;
    }
    return (
      blunders.find((b) => b.position_key === key && b.move_san === san) ??
      null
    );
  }, [moveIndex, positions, moveDetails, blunders]);

  const currentGameKey = useMemo(() => {
    if (moveIndex === 0) {
      return 'start';
    }
    if (moveIndex > moveDetails.length) {
      return null;
    }
    const detail = moveDetails[moveIndex - 1];
    if (!detail) {
      return null;
    }
    return `${moveSans.slice(0, moveIndex - 1).join(' ')}|${detail.from}${detail.to}${detail.promotion ?? ''}`;
  }, [moveIndex, moveDetails, moveSans]);

  // Live result wins while it describes exactly what's on screen (the
  // studied game move, or the deviation just played).
  const showLive =
    explore.armed &&
    explore.live.classification !== null &&
    (explore.deviation !== null || explore.live.key === currentGameKey);

  const displaySan =
    explore.deviation?.san ??
    (moveIndex > 0 ? (moveDetails[moveIndex - 1]?.san ?? null) : null);
  const displayPly =
    moveIndex + (explore.deviation ? explore.deviation.sans.length : 0);

  const lastMoveSquares = useMemo(() => {
    if (explore.deviation) {
      return { from: explore.deviation.from, to: explore.deviation.to };
    }
    if (moveIndex <= 0 || moveIndex > moveDetails.length) {
      return null;
    }
    const detail = moveDetails[moveIndex - 1];
    return detail ? { from: detail.from, to: detail.to } : null;
  }, [explore.deviation, moveIndex, moveDetails]);
  const boardSquareStyles = useMemo(() => {
    if (!lastMoveSquares) {
      return undefined;
    }
    return {
      [lastMoveSquares.from]: {
        backgroundColor: 'rgba(255, 213, 105, 0.30)',
      },
      [lastMoveSquares.to]: {
        backgroundColor: 'rgba(255, 213, 105, 0.40)',
      },
    };
  }, [lastMoveSquares]);

  const [playing, setPlaying] = useState(false);
  const { resetDeviation } = explore;
  useEffect(() => {
    if (!playing || phase !== 'ready' || moveIndex >= lastMoveIndex) {
      return;
    }
    const timer = setTimeout(() => {
      resetDeviation();
      setMoveIndex(moveIndex + 1);
    }, 1000);
    return () => clearTimeout(timer);
  }, [playing, phase, moveIndex, lastMoveIndex, resetDeviation]);
  const togglePlay = () => {
    if (playing) {
      setPlaying(false);
      return;
    }
    if (moveIndex >= lastMoveIndex) {
      explore.resetDeviation();
      setMoveIndex(0);
    }
    setPlaying(true);
  };

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') {
        return;
      }
      const target = event.target as HTMLElement | null;
      if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA')) {
        return;
      }
      event.preventDefault();
      goToPly(moveIndex + (event.key === 'ArrowRight' ? 1 : -1));
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  });

  async function copyFen() {
    try {
      await navigator.clipboard.writeText(displayedFen);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard can be unavailable (permissions / insecure context).
    }
  }

  const moveLabel =
    trap.example_move_san || (trap.moves.length > 0 ? trap.moves[0] : '?');
  const moveRange =
    trap.move_number_min === trap.move_number_max
      ? `move ${trap.move_number_min}`
      : `moves ${trap.move_number_min}-${trap.move_number_max}`;
  const classificationTone =
    trap.example_classification === 'blunder'
      ? 'border-rose-400/30 bg-rose-500/10 text-rose-200'
      : 'border-amber-400/30 bg-amber-500/10 text-amber-200';

  return (
    <ModalShell title="Recurring blunder" onClose={onClose}>
      <p className="mt-1 text-[11px] text-[#f7e5c6]/50">
        {moveRange} · {trap.game_count} game{trap.game_count === 1 ? '' : 's'} ·{' '}
        {sideToMove === 'white' ? 'White' : 'Black'} to move
      </p>
      <p
        className={`mt-1 text-[11px] ${trap.exploitable ? 'text-emerald-200/80' : 'text-[#f7e5c6]/40'}`}
      >
        {trap.exploitable
          ? 'The sparring bot plays toward this position.'
          : 'Observed pattern - the bot won’t steer here.'}
      </p>

      <div className="mt-2 flex min-h-10 items-center justify-between gap-2">
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-1">
          <ModalMoveReadout ply={displayPly} total={lastMoveIndex} san={displaySan} />
          {storedBlunder && !showLive && (
            <ModalLabelChip
              classification={storedBlunder.classification}
              title="From stored analysis - no engine call"
            />
          )}
          {showLive && explore.live.classification && (
            <ModalLabelChip
              classification={explore.live.classification}
              title="Live engine label for the position on screen"
            />
          )}
          {explore.armed && explore.live.pending && !showLive && (
            <span className="inline-flex items-center gap-1.5 text-[11px] text-[#f7e5c6]/60">
              <span className="h-2.5 w-2.5 animate-spin rounded-full border-2 border-white/20 border-t-white" />
              Analyzing…
            </span>
          )}
          {showLive && (
            <ModalEvalChip cp={explore.live.evalCp} mate={explore.live.evalMate} />
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <ModalCopyFenButton onCopy={copyFen} copied={copied} />
          <ModalExploreToggle armed={explore.armed} onToggle={explore.toggleArmed} />
        </div>
      </div>
      {explore.armed && explore.live.error && (
        <p className="mt-1 text-[11px] leading-5 text-amber-300/90">
          {explore.live.error}
        </p>
      )}

      <div
        className={`mt-3 overflow-hidden rounded-2xl border border-black/50 ${modalBoardWidthClass}`}
      >
        <div className="relative aspect-square w-full">
          <Chessboard
            options={{
              ...modalBoardOptions,
              id: trapBoardId,
              position: displayedFen,
              boardOrientation: erringSide,
              animationDurationInMs: explore.armed ? 150 : 0,
              squareStyles: boardSquareStyles,
              allowDragging: explore.armed,
              onPieceDrop: explore.armed
                ? (args: PieceDropHandlerArgs) => {
                    if (!args.sourceSquare || !args.targetSquare) {
                      return false;
                    }
                    return explore.playDeviation(
                      args.sourceSquare,
                      args.targetSquare,
                      displayedFen,
                      moveSans.slice(0, moveIndex),
                      explore.deviation?.sans ?? []
                    );
                  }
                : undefined,
              arrows: explore.arrows,
            }}
          />
        </div>
      </div>

      {explore.deviation && (
        <div className="mt-2 flex items-center justify-between gap-2 rounded-xl border border-emerald-400/25 bg-emerald-500/[0.07] px-3 py-2">
          <span className="min-w-0 truncate text-[11px] text-emerald-100/90">
            Your line: {explore.deviation.sans.join(' ')}
          </span>
          <button
            type="button"
            onClick={explore.resetDeviation}
            className="shrink-0 cursor-pointer rounded-full border border-emerald-400/30 bg-emerald-500/10 px-3 py-1 text-[11px] font-semibold text-emerald-200 transition-colors duration-200 hover:bg-emerald-500/20 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#efd9a7]"
          >
            Back to game
          </button>
        </div>
      )}

      <div className="mt-2">
        <ModalWoodNav
          disabled={phase !== 'ready'}
          onFirst={() => goToPly(0)}
          onPrev={() => goToPly(moveIndex - 1)}
          onPlay={togglePlay}
          onNext={() => goToPly(moveIndex + 1)}
          onLast={() => goToPly(lastMoveIndex)}
          playing={playing}
        />
      </div>

      <div className="mt-2 flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate text-sm font-semibold text-[#f7e5c6]">
            Played {moveLabel}
          </span>
          <span
            className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.14em] ${classificationTone}`}
          >
            {trap.example_classification}
          </span>
        </div>
        {phase === 'error' && (
          <span className="shrink-0 text-[10px] text-[#f7e5c6]/40">
            Game unavailable
          </span>
        )}
      </div>
    </ModalShell>
  );
}

function OpeningReplayModal({
  provider,
  username,
  family,
  color,
  onClose,
}: {
  provider: 'lichess' | 'chesscom';
  username: string;
  family: string;
  color: 'white' | 'black';
  onClose: () => void;
}) {
  const [phase, setPhase] = useState<'loading' | 'error' | 'ready'>('loading');
  const [games, setGames] = useState<OpeningGameSummary[]>([]);
  const [gameIndex, setGameIndex] = useState(0);
  const [gamePhase, setGamePhase] = useState<'loading' | 'error' | 'ready'>(
    'loading'
  );
  const [game, setGame] = useState<OpeningGame | null>(null);
  const [positions, setPositions] = useState<string[]>([START_FEN]);
  const [moveIndex, setMoveIndex] = useState(0);
  const [moveSans, setMoveSans] = useState<string[]>([]);
  const [moveDetails, setMoveDetails] = useState<VerboseHistoryMove[]>([]);
  const [blunders, setBlunders] = useState<OpeningGameBlunder[]>([]);
  const gameCache = useRef(new Map<string, OpeningGame>());

  // Load the bucket's ordered game list once; open on the server-chosen
  // game (newest blunder-loss, else newest loss, else the newest game).
  useEffect(() => {
    let cancelled = false;
    gameCache.current.clear();

    async function loadGames() {
      try {
        const response = await fetch(
          `/api/train/opponent-opening-games?provider=${provider}&opponent_username=${encodeURIComponent(username)}&family=${encodeURIComponent(family)}&color=${color}`,
          { cache: 'no-store' }
        );
        if (!response.ok) {
          if (!cancelled) {
            setPhase('error');
          }
          return;
        }
        const data = (await response.json()) as OpeningGamesResponse;
        if (cancelled) {
          return;
        }
        const initialIndex = data.games.findIndex(
          (entry) => entry.game_id === data.initial_game_id
        );
        setGames(data.games);
        setGameIndex(initialIndex >= 0 ? initialIndex : 0);
        setPhase('ready');
      } catch {
        if (!cancelled) {
          setPhase('error');
        }
      }
    }

    loadGames();
    return () => {
      cancelled = true;
    };
  }, [provider, username, family, color]);

  // Fetch (or reuse) the selected game's PGN and replay it into positions.
  // Games are cached by id, so stepping back and forth is instant.
  useEffect(() => {
    if (phase !== 'ready') {
      return;
    }
    const selected = games[gameIndex];
    if (!selected) {
      return;
    }
    let cancelled = false;

    async function loadGame() {
      setGamePhase('loading');
      try {
        let next = gameCache.current.get(selected.game_id) ?? null;
        if (!next) {
          const response = await fetch(
            `/api/train/opponent-opening-game?game_id=${encodeURIComponent(selected.game_id)}`,
            { cache: 'no-store' }
          );
          if (!response.ok) {
            if (!cancelled) {
              setGamePhase('error');
            }
            return;
          }
          next = (await response.json()) as OpeningGame;
          gameCache.current.set(selected.game_id, next);
        }
        if (cancelled) {
          return;
        }
        // Replay locally: positions[i] is the FEN after i plies, so the
        // position before ply p is positions[p - 1] (the blunder jump).
        // Keep the SAN history and verbose details alongside for explore.
        const replay = new Chess();
        replay.loadPgn(next.pgn);
        const stepper = new Chess();
        const nextPositions = [START_FEN];
        for (const san of replay.history()) {
          stepper.move(san);
          nextPositions.push(stepper.fen());
        }
        setGame(next);
        setPositions(nextPositions);
        setMoveSans(replay.history());
        setMoveDetails(
          replay.history({ verbose: true }) as unknown as VerboseHistoryMove[]
        );
        setBlunders(next.blunders ?? []);
        setMoveIndex(0);
        setGamePhase('ready');
      } catch {
        if (!cancelled) {
          setGamePhase('error');
        }
      }
    }

    loadGame();
    return () => {
      cancelled = true;
    };
  }, [phase, games, gameIndex]);

  const summary = games[gameIndex] ?? null;
  const lastMoveIndex = positions.length - 1;
  const position = positions[moveIndex] ?? START_FEN;
  const navButtonClass =
    'flex h-7 w-7 items-center justify-center rounded-lg border border-[#f7e5c6]/20 bg-black/40 text-xs text-[#f7e5c6]/80 transition hover:bg-black/60 disabled:pointer-events-none disabled:opacity-35';

  // Explore is always on in this viewer: every studied position gets a live
  // label plus the side-to-move's best reply as a board arrow, and dragging
  // a different move analyzes the deviation. There is no toggle.
  const explore = useModalExplore({
    active: gamePhase === 'ready' && game !== null && positions.length > 1,
    gameSans: moveSans,
    gameDetails: moveDetails,
    basePly: moveIndex,
    resetToken: game?.game_id ?? 'none',
    defaultArmed: true,
  });
  const displayedFen = explore.deviation?.fen ?? position;
  // Unique per mount (same co-mount reason as the trap board).
  const openingBoardId = `opening-board-${useId().replace(/:/g, '')}`;

  function goToPly(ply: number) {
    explore.resetDeviation();
    setMoveIndex(Math.max(0, Math.min(positions.length - 1, ply)));
  }

  // Stored error badge for the arriving move (free, no engine).
  const storedBlunder = useMemo(() => {
    if (moveIndex <= 0 || moveIndex > moveDetails.length) {
      return null;
    }
    const key = positionKey(positions[moveIndex - 1] ?? '');
    const san = moveDetails[moveIndex - 1]?.san;
    if (!key || !san) {
      return null;
    }
    return (
      blunders.find((b) => b.position_key === key && b.move_san === san) ??
      null
    );
  }, [moveIndex, positions, moveDetails, blunders]);

  const currentGameKey = useMemo(() => {
    if (moveIndex === 0) {
      return 'start';
    }
    if (moveIndex > moveDetails.length) {
      return null;
    }
    const detail = moveDetails[moveIndex - 1];
    if (!detail) {
      return null;
    }
    return `${moveSans.slice(0, moveIndex - 1).join(' ')}|${detail.from}${detail.to}${detail.promotion ?? ''}`;
  }, [moveIndex, moveDetails, moveSans]);

  const showLive =
    explore.armed &&
    explore.live.classification !== null &&
    (explore.deviation !== null || explore.live.key === currentGameKey);

  const displayPly =
    moveIndex + (explore.deviation ? explore.deviation.sans.length : 0);

  const lastMoveSquares = useMemo(() => {
    if (explore.deviation) {
      return { from: explore.deviation.from, to: explore.deviation.to };
    }
    if (moveIndex <= 0 || moveIndex > moveDetails.length) {
      return null;
    }
    const detail = moveDetails[moveIndex - 1];
    return detail ? { from: detail.from, to: detail.to } : null;
  }, [explore.deviation, moveIndex, moveDetails]);
  const boardSquareStyles = useMemo(() => {
    if (!lastMoveSquares) {
      return undefined;
    }
    return {
      [lastMoveSquares.from]: {
        backgroundColor: 'rgba(255, 213, 105, 0.30)',
      },
      [lastMoveSquares.to]: {
        backgroundColor: 'rgba(255, 213, 105, 0.40)',
      },
    };
  }, [lastMoveSquares]);

  const [playing, setPlaying] = useState(false);
  const { resetDeviation } = explore;
  useEffect(() => {
    if (!playing || gamePhase !== 'ready' || moveIndex >= lastMoveIndex) {
      return;
    }
    const timer = setTimeout(() => {
      resetDeviation();
      setMoveIndex(moveIndex + 1);
    }, 1000);
    return () => clearTimeout(timer);
  }, [playing, gamePhase, moveIndex, lastMoveIndex, resetDeviation]);
  const togglePlay = () => {
    if (playing) {
      setPlaying(false);
      return;
    }
    if (moveIndex >= lastMoveIndex) {
      explore.resetDeviation();
      setMoveIndex(0);
    }
    setPlaying(true);
  };

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') {
        return;
      }
      const target = event.target as HTMLElement | null;
      if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA')) {
        return;
      }
      event.preventDefault();
      goToPly(moveIndex + (event.key === 'ArrowRight' ? 1 : -1));
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  });

  const goToGame = (nextIndex: number) => {
    if (nextIndex < 0 || nextIndex >= games.length || nextIndex === gameIndex) {
      return;
    }
    setPlaying(false);
    explore.resetDeviation();
    setGameIndex(nextIndex);
  };

  return (
    <ModalShell
      title={`${family} · as ${color === 'white' ? 'White' : 'Black'}`}
      onClose={onClose}
      hideTitle
    >
      {phase === 'loading' && (
        <div className="mt-6 flex items-center justify-center gap-2 py-10 text-xs text-[#f7e5c6]/60">
          <span className="h-3 w-3 rounded-full border-2 border-[#f7e5c6]/35 border-t-[#f7e5c6] animate-spin" />
          Loading games…
        </div>
      )}

      {phase === 'error' && (
        <div className="mt-4 rounded-xl border border-amber-400/30 bg-amber-400/10 px-3 py-3 text-xs leading-relaxed text-amber-200">
          No games found for this opening. The opponent may have been
          re-imported since this row was built - refresh and try again.
        </div>
      )}

      {phase === 'ready' && summary && (
        <>
          <div
            className={`mt-3 overflow-hidden rounded-2xl border border-black/50 ${modalBoardWidthClass}`}
          >
            <div className="relative aspect-square w-full">
              {gamePhase === 'ready' && game ? (
                <Chessboard
                  options={{
                    ...modalBoardOptions,
                    id: openingBoardId,
                    position: displayedFen,
                    boardOrientation: color,
                    animationDurationInMs: explore.armed ? 150 : 0,
                    squareStyles: boardSquareStyles,
                    allowDragging: explore.armed,
                    onPieceDrop: explore.armed
                      ? (args: PieceDropHandlerArgs) => {
                          if (!args.sourceSquare || !args.targetSquare) {
                            return false;
                          }
                          return explore.playDeviation(
                            args.sourceSquare,
                            args.targetSquare,
                            displayedFen,
                            moveSans.slice(0, moveIndex),
                            explore.deviation?.sans ?? []
                          );
                        }
                      : undefined,
                    arrows: explore.arrows,
                  }}
                />
              ) : (
                <div className="flex h-full w-full flex-col items-center justify-center gap-2 bg-black/40 text-xs text-[#f7e5c6]/60">
                  {gamePhase === 'error' ? (
                    'Could not load this game.'
                  ) : (
                    <>
                      <span className="h-3 w-3 rounded-full border-2 border-[#f7e5c6]/35 border-t-[#f7e5c6] animate-spin" />
                      Loading game…
                    </>
                  )}
                </div>
              )}
            </div>
          </div>

          {/* Slim status bar: game stepper + move counter on the left,
              live explore label on the right. Explore is always on here, so
              there is no toggle row - one line covers context plus engine
              feedback and the card fits short viewports without scrolling. */}
          <div className="mt-2 flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5">
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => goToGame(gameIndex - 1)}
                disabled={gameIndex <= 0}
                aria-label="Previous game"
                className={navButtonClass}
              >
                ‹
              </button>
              <button
                type="button"
                onClick={() => goToGame(gameIndex + 1)}
                disabled={gameIndex >= games.length - 1}
                aria-label="Next game"
                className={navButtonClass}
              >
                ›
              </button>
              <span className="ml-1 text-[11px] tabular-nums text-[#f7e5c6]/50">
                Game {gameIndex + 1} / {games.length}
              </span>
              {gamePhase === 'ready' && game && (
                <>
                  <span
                    className="h-1 w-1 rounded-full bg-[#f7e5c6]/25"
                    aria-hidden
                  />
                  <span className="text-[11px] tabular-nums text-[#f7e5c6]/50">
                    Move {displayPly} / {lastMoveIndex}
                  </span>
                </>
              )}
            </div>
            {gamePhase === 'ready' && game && (
              <div className="flex min-w-0 flex-wrap items-center gap-1.5">
                {storedBlunder && !showLive && (
                  <ModalLabelChip
                    classification={storedBlunder.classification}
                    title="From stored analysis - no engine call"
                  />
                )}
                {showLive && explore.live.classification && (
                  <ModalLabelChip
                    classification={explore.live.classification}
                    title="Live engine label for the position on screen"
                  />
                )}
                {explore.live.pending && !showLive && (
                  <span className="inline-flex items-center gap-1.5 text-[11px] text-[#f7e5c6]/60">
                    <span className="h-2.5 w-2.5 animate-spin rounded-full border-2 border-white/20 border-t-white" />
                    Analyzing…
                  </span>
                )}
                {showLive && (
                  <ModalEvalChip
                    cp={explore.live.evalCp}
                    mate={explore.live.evalMate}
                  />
                )}
              </div>
            )}
          </div>
          {explore.live.error && (
            <p className="mt-1 text-[11px] leading-5 text-amber-300/90">
              {explore.live.error}
            </p>
          )}

          {explore.deviation && (
            <div className="mt-2 flex items-center justify-between gap-2 rounded-xl border border-emerald-400/25 bg-emerald-500/[0.07] px-3 py-2">
              <span className="min-w-0 truncate text-[11px] text-emerald-100/90">
                Your line: {explore.deviation.sans.join(' ')}
              </span>
              <button
                type="button"
                onClick={explore.resetDeviation}
                className="shrink-0 cursor-pointer rounded-full border border-emerald-400/30 bg-emerald-500/10 px-3 py-1 text-[11px] font-semibold text-emerald-200 transition-colors duration-200 hover:bg-emerald-500/20 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#efd9a7]"
              >
                Back to game
              </button>
            </div>
          )}

          <div className="mt-2">
            <ModalWoodNav
              disabled={gamePhase !== 'ready' || !game}
              onFirst={() => goToPly(0)}
              onPrev={() => goToPly(moveIndex - 1)}
              onPlay={togglePlay}
              onNext={() => goToPly(moveIndex + 1)}
              onLast={() => goToPly(lastMoveIndex)}
              playing={playing}
            />
          </div>

          {/* Game meta: result badge + date come from the summary; players,
              score and time class need the fetched PGN. */}
          <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-[#f7e5c6]/60">
            <OpeningResultBadge result={summary.result} />
            <span>{new Date(summary.end_time * 1000).toLocaleDateString()}</span>
            {gamePhase === 'ready' && game && (
              <>
                <span
                  className="h-1 w-1 rounded-full bg-[#f7e5c6]/25"
                  aria-hidden
                />
                <span className="truncate">
                  {game.white} vs {game.black}
                </span>
                <span
                  className="h-1 w-1 rounded-full bg-[#f7e5c6]/25"
                  aria-hidden
                />
                <span>{game.result}</span>
                <span
                  className="h-1 w-1 rounded-full bg-[#f7e5c6]/25"
                  aria-hidden
                />
                <span>{game.time_class}</span>
              </>
            )}
          </div>

          {gamePhase === 'ready' && game && summary.first_blunder && (
            <div className="mt-2 flex items-center justify-between gap-3 rounded-xl border border-black/30 bg-black/30 px-3 py-2">
              <span className="min-w-0 truncate text-[11px] text-[#f7e5c6]/70">
                First {summary.first_blunder.classification}: move{' '}
                {summary.first_blunder.move_number} ·{' '}
                {summary.first_blunder.move_san}
              </span>
              <button
                type="button"
                onClick={() =>
                  goToPly(Math.max(0, (summary.first_blunder?.ply ?? 1) - 1))
                }
                className="shrink-0 rounded-full border border-rose-400/30 bg-rose-500/10 px-3 py-1.5 text-[11px] font-semibold text-rose-200 transition hover:bg-rose-500/20"
              >
                Jump to blunder
              </button>
            </div>
          )}

          {gamePhase === 'ready' && game && !summary.first_blunder && (
            <p className="mt-3 text-[11px] leading-relaxed text-[#f7e5c6]/45">
              {summary.analyzed
                ? 'No mistakes or blunders found in this game.'
                : 'This game was not analyzed (the Stockfish pass covers the most recent games), so there is no move review for it.'}
            </p>
          )}
        </>
      )}
    </ModalShell>
  );
}

function OpeningResultBadge({
  result,
}: {
  result: OpeningGameSummary['result'];
}) {
  const meta =
    result === 'win'
      ? {
          label: 'Win',
          className: 'border-emerald-400/30 bg-emerald-400/10 text-emerald-200',
        }
      : result === 'loss'
        ? {
            label: 'Loss',
            className: 'border-rose-400/30 bg-rose-500/10 text-rose-200',
          }
        : result === 'draw'
          ? {
              label: 'Draw',
              className: 'border-[#f7e5c6]/20 bg-white/5 text-[#f7e5c6]/70',
            }
          : {
              label: 'Unfinished',
              className: 'border-[#f7e5c6]/15 bg-black/30 text-[#f7e5c6]/45',
            };

  return (
    <span
      className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${meta.className}`}
    >
      {meta.label}
    </span>
  );
}

function PreferredTimeControl({
  distribution,
  mostPlayed,
}: {
  distribution: Record<string, number> | null;
  mostPlayed: string | null;
}) {
  const entries = distribution
    ? Object.entries(distribution)
        .map(([label, fraction]) => ({
          label,
          fraction,
          color: TC_COLOR[label] ?? DEFAULT_TC_COLOR,
        }))
        .sort((a, b) => b.fraction - a.fraction)
    : [];
  const primaryEntry =
    entries.find((entry) => entry.label === mostPlayed) ?? entries[0] ?? null;

  return (
    <section className="rounded-[18px] border border-[#f7e5c6]/10 bg-black/25 p-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]">
      <SectionHeader icon={<StopwatchReliefIcon />} title="Preferred Time Control" />

      {entries.length === 0 ? (
        <div className="mt-3">
          <EmptyHint text="No time-control data yet." />
        </div>
      ) : (
        <div className="mt-3 space-y-2">
          {entries.map(({ label, fraction, color }) => {
            const isPrimary = label === primaryEntry?.label;
            const percentage = Math.round(fraction * 100);
            return (
              <div
                key={label}
                className={`rounded-xl px-2 py-1.5 transition-colors ${
                  isPrimary
                    ? 'border border-[#d9b87c]/10 bg-[#d9b87c]/[0.04]'
                    : 'hover:bg-white/[0.025]'
                }`}
              >
                <div className="flex items-center justify-between gap-3">
                  <div className="flex min-w-0 items-center gap-2">
                    <span
                      aria-hidden
                      className="h-2 w-2 shrink-0 rounded-full shadow-[0_0_8px_rgba(255,255,255,0.12)]"
                      style={{ backgroundColor: color }}
                    />
                    <span className={`truncate text-xs ${isPrimary ? 'font-semibold text-[#f7e5c6]' : 'text-[#f7e5c6]/65'}`}>
                      {label}
                    </span>
                  </div>
                  <span className={`shrink-0 text-xs tabular-nums ${isPrimary ? 'font-semibold text-[#efd9a7]' : 'text-[#f7e5c6]/60'}`}>
                    {percentage}%
                  </span>
                </div>
                <div className="ml-4 mt-1.5 h-1 overflow-hidden rounded-full bg-black/40">
                  <div
                    className="h-full rounded-full transition-[width] duration-500 ease-out"
                    style={{
                      width: `${Math.max(0, Math.min(100, fraction * 100))}%`,
                      backgroundColor: color,
                    }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

function SectionHeader({ icon, title }: { icon?: React.ReactNode; title: string }) {
  return (
    <div className="flex items-center gap-2 border-b border-white/5 pb-2 text-[#f7e5c6]">
      {icon && <span className="text-emerald-300/75">{icon}</span>}
      <h3 className="text-[11px] font-semibold uppercase tracking-[0.22em]">
        {title}
      </h3>
    </div>
  );
}

function OpeningWeaknessReliefIcon() {
  return (
    <ReliefBadge size="xs">
      <svg
        width="14"
        height="14"
        viewBox="0 0 20 20"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <path d="M3.5 16.5h13" />
        <path d="M5 14V5" />
        <path d="M10 14V8" />
        <path d="M15 14v-3" />
        <path d="m4.5 5 4 2 3.5 3 4 2.5" />
      </svg>
    </ReliefBadge>
  );
}

function EmptyHint({ text }: { text: string }) {
  return (
    <div className="rounded-2xl border border-dashed border-[#f7e5c6]/15 bg-black/20 px-3 py-3 text-center text-[11px] text-[#f7e5c6]/45">
      {text}
    </div>
  );
}

// In-game action icons, same glyphs as the engine sparring page.
function CloseIcon() {
  return (
    <svg
      className="h-4 w-4"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M18 6 6 18M6 6l12 12" />
    </svg>
  );
}

function MovesIcon() {
  return (
    <svg
      className="h-4 w-4"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M8 6h13M8 12h13M8 18h13" />
      <path d="M3.5 6h.01M3.5 12h.01M3.5 18h.01" />
    </svg>
  );
}

function RatingIcon() {
  return (
    <svg
      className="h-4 w-4"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M3 17l6-6 4 4 8-8" />
      <path d="M14 7h7v7" />
    </svg>
  );
}

function FlagIcon() {
  return (
    <svg
      className="h-4 w-4 shrink-0"
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="2"
      viewBox="0 0 24 24"
      aria-hidden
    >
      <path d="M4 22V4" />
      <path d="M4 4c3-2 6 2 9 0s5-2 7-1v9c-2-1-4-1-7 1s-6 0-9 0" />
    </svg>
  );
}

function BulbIcon() {
  return (
    <svg
      className="h-4 w-4 shrink-0"
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="2"
      viewBox="0 0 24 24"
      aria-hidden
    >
      <path d="M9 18h6" />
      <path d="M10 22h4" />
      <path d="M8.5 14.5A6 6 0 1 1 15.5 14c-.8.5-1.5 1.3-1.5 2.2V17h-4v-.8c0-.7-.5-1.3-1.5-1.7Z" />
    </svg>
  );
}

// The "show move" mark for the second Hint press.
function EyeIcon() {
  return (
    <svg
      className="h-4 w-4 shrink-0"
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="2"
      viewBox="0 0 24 24"
      aria-hidden
    >
      <path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12Z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

function UndoIcon() {
  return (
    <svg
      className="h-4 w-4 shrink-0"
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="2"
      viewBox="0 0 24 24"
      aria-hidden
    >
      <path d="M3 7v6h6" />
      <path d="M21 17a9 9 0 0 0-15-6.7L3 13" />
    </svg>
  );
}

function ReliefBadge({
  children,
  size,
}: {
  children: React.ReactNode;
  size: 'xs' | 'sm' | 'md';
}) {
  const sizeClass =
    size === 'xs'
      ? 'h-7 w-7 text-[14px]'
      : size === 'sm'
        ? 'h-5 w-5 text-[13px]'
        : 'h-10 w-10 text-[24px]';
  return (
    <span
      className={`relative inline-flex shrink-0 items-center justify-center rounded-full border border-[#f7e5c6]/20 bg-[radial-gradient(circle_at_34%_26%,#fff3cf_0%,#d6a95e_32%,#7a4a1d_68%,#1a0d05_100%)] text-[#2a1609] shadow-[inset_0_1px_1px_rgba(255,255,255,0.55),inset_0_-2px_4px_rgba(0,0,0,0.55),0_8px_18px_rgba(0,0,0,0.35)] ${sizeClass}`}
      aria-hidden
    >
      <span className="absolute inset-[18%] rounded-full bg-black/10 blur-[1px]" />
      <span className="relative -mt-px flex items-center justify-center leading-none [filter:drop-shadow(0_1px_0_rgba(255,240,190,0.55))_drop-shadow(0_2px_1px_rgba(0,0,0,0.45))]">
        {children}
      </span>
    </span>
  );
}

function KnightReliefIcon({ size }: { size: 'sm' | 'md' }) {
  return (
    <ReliefBadge size={size}>
      <span className="font-serif font-black">♞</span>
    </ReliefBadge>
  );
}

function StopwatchReliefIcon() {
  return (
    <ReliefBadge size="sm">
      <svg
        width="13"
        height="13"
        viewBox="0 0 20 20"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <circle cx="10" cy="11" r="6.3" />
        <path d="M10 11V7.5M10 11l2.5 1.5M8 2.5h4M10 2.5v2" />
      </svg>
    </ReliefBadge>
  );
}

function profileKey(profile: OpponentProfile | undefined) {
  if (!profile) {
    return '';
  }
  return `${profile.provider}:${profile.opponent_username}`;
}

function displayProvider(provider: 'lichess' | 'chesscom') {
  return provider === 'lichess' ? 'Lichess' : 'Chess.com';
}

type GameEnding =
  | 'checkmate'
  | 'stalemate'
  | 'insufficient_material'
  | 'fifty_move_rule'
  | 'threefold_repetition';

/** First 4 FEN fields (board/turn/castling/ep), counters excluded: the key
 *  a position repeats on. */
function positionKey(fen: string): string {
  return fen.split(/\s+/).slice(0, 4).join(' ');
}

/**
 * The rule that ended the game, or null while it is live.
 *
 * chess.js's own isThreefoldRepetition() cannot see the repetition here:
 * every ply is applied on a fresh Chess built from a FEN, which resets its
 * position counter. The tracked `positionKeys` (oldest first, current last)
 * are counted instead -- on the third occurrence the draw is taken. The
 * other rules are FEN-readable and stay chess.js's.
 */
function detectGameEnding(
  game: Chess,
  positionKeys: string[]
): GameEnding | null {
  if (game.isCheckmate()) return 'checkmate';
  if (game.isStalemate()) return 'stalemate';
  if (game.isInsufficientMaterial()) return 'insufficient_material';
  if (game.isDrawByFiftyMoves()) return 'fifty_move_rule';
  const current = positionKeys[positionKeys.length - 1];
  if (positionKeys.filter((key) => key === current).length >= 3) {
    return 'threefold_repetition';
  }
  return null;
}

// Rebuild the position after `ply` plies by replaying the SAN line. Games
// here are short, so replaying from the start is cheaper than caching FEN
// chains and keeps every branch point exact.
function buildGameAt(sans: string[], ply: number): Chess {
  return replaySans(sans.slice(0, ply));
}

// Replay a SAN line from the start position, keeping the full move history
// on the resulting game (unlike `new Chess(fen)`, which starts history empty).
function replaySans(sans: string[]): Chess {
  const rebuilt = new Chess();
  for (const san of sans) {
    rebuilt.move(san);
  }
  return rebuilt;
}

// A move cell in the in-game history box; the reviewed ply carries the amber
// tint. Same as the engine sparring page.
function moveCellClass(active: boolean): string {
  return `flex-1 truncate rounded-md px-2 py-1 text-left text-[13px] font-semibold transition ${
    active
      ? 'bg-[#eacb90]/30 text-[#f7e5c6]'
      : 'text-[#f7e5c6]/80 hover:bg-white/10'
  }`;
}

function uciToMove(uci: string) {
  return {
    from: uci.slice(0, 2),
    to: uci.slice(2, 4),
    promotion: uci.slice(4, 5) || undefined,
  };
}
