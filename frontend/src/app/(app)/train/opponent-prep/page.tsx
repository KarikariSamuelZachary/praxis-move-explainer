'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Chess, Square } from 'chess.js';
import dynamic from 'next/dynamic';
import type { SquareRenderer } from 'react-chessboard';

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
};

type SparringMoveResponse = {
  move_uci: string;
  move_san: string;
  source: 'in_book' | 'playing_naturally';
  opponent_elo: number;
  repertoire_frequency?: number | null;
};

type OpeningGameBlunder = {
  move_number: number;
  ply: number;
  move_san: string;
  classification: 'mistake' | 'blunder';
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
};

// 'checking' is the brief first status fetch; 'polling' means analyzed <
// total; 'complete' means every analyzed game is in and traps were
// refreshed; 'idle' is the no-job/error/total-0 fallback (old empty state).
type TrapsPhase = 'idle' | 'checking' | 'polling' | 'complete';

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
  const [isStarted, setIsStarted] = useState(false);
  const [isThinking, setIsThinking] = useState(false);
  const [status, setStatus] = useState<BotSource>('ready');
  const [message, setMessage] = useState<string | null>(null);
  const [lastMove, setLastMove] = useState<SparringMoveResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selectedSquare, setSelectedSquare] = useState<string | null>(null);
  const [premove, setPremove] = useState<Premove | null>(null);
  const [importWarnings, setImportWarnings] = useState<string[]>([]);
  const [timeControl, setTimeControl] = useState<TimeClassKey | ''>('');
  const gameRef = useRef(game);
  const botMoveInFlightRef = useRef(false);
  // Every position key since the game started. The game is rebuilt from a
  // FEN on each ply, which resets chess.js's own repetition counter, so
  // threefold repetition is counted from these keys instead. Reset with the
  // game (startGame/resetGame).
  const positionKeysRef = useRef<string[]>([positionKey(START_FEN)]);

  useEffect(() => {
    gameRef.current = game;
  }, [game]);

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
      // Malformed payload or storage unavailable — nothing to show.
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

  // The right card no longer shows a per-move log - the mockup replaces
  // it with three aggregate sections (Openings / Traps / Time Control).
  // The latest bot move's SAN still surfaces in the StatusStrip via the
  // `lastMove` API response, so we don't need a derived `moveHistory`
  // here.
  // checkmate | stalemate | insufficient material | fifty-move rule |
  // threefold repetition (the last one via the tracked keys -- see
  // detectGameEnding). Any of them ends the game.
  const ending = detectGameEnding(game, positionKeysRef.current);
  const gameOver = ending !== null;
  const endingText = ending ? gameEndingLabel(ending, game) : null;

  const humanCanMove =
    isStarted &&
    !isThinking &&
    !gameOver &&
    (game.turn() === 'w' ? 'white' : 'black') === humanColor;

  // Premoving: while the bot is on the move the user may commit a move that
  // is played automatically (when still legal) the moment it becomes their
  // turn, so sparring keeps a bullet-like rhythm.
  const canPremove = isStarted && !gameOver && !humanCanMove;
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

  const premoveSan = useMemo<string | null>(() => {
    if (!premove || !premoveProbeGame) {
      return null;
    }
    try {
      const probe = new Chess(premoveProbeGame.fen());
      return probe.move({ from: premove.from, to: premove.to, promotion: 'q' })?.san ?? null;
    } catch {
      return null;
    }
  }, [premove, premoveProbeGame]);

  const requestBotMove = useCallback(async () => {
    if (!selectedProfile || botMoveInFlightRef.current || gameRef.current.isGameOver()) {
      return;
    }

    botMoveInFlightRef.current = true;
    setIsThinking(true);
    setStatus('thinking');
    setMessage(null);

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
      const nextGame = new Chess(gameRef.current.fen());
      nextGame.move(uciToMove(data.move_uci));
      positionKeysRef.current.push(positionKey(nextGame.fen()));
      setGame(nextGame);
      setLastMove(data);
      setStatus(data.source);
    } catch (error) {
      setStatus('error');
      setMessage(error instanceof Error ? error.message : 'Failed to get a sparring move.');
    } finally {
      botMoveInFlightRef.current = false;
      setIsThinking(false);
    }
  }, [botColor, selectedProfile, timeControl]);

  // Play a stored premove as soon as it becomes the human's turn. Premoves
  // that stopped being legal (the bot blocked the path, captured the piece
  // or delivered check) are discarded, matching lichess/chess.com behaviour.
  useEffect(() => {
    if (!premove) {
      return;
    }
    if (!isStarted || gameOver) {
      setPremove(null);
      return;
    }

    const turnColor = game.turn() === 'w' ? 'white' : 'black';
    if (turnColor !== humanColor) {
      return;
    }

    setPremove(null);

    const nextGame = new Chess(game.fen());
    let move: ReturnType<typeof nextGame.move> | null = null;
    try {
      move = nextGame.move({ from: premove.from, to: premove.to, promotion: 'q' });
    } catch {
      move = null;
    }

    if (move) {
      positionKeysRef.current.push(positionKey(nextGame.fen()));
      setGame(nextGame);
      setLastMove(null);
      setMessage(null);
      setStatus('ready');
      setSelectedSquare(null);
    }
  }, [game, gameOver, humanColor, isStarted, premove]);

  useEffect(() => {
    if (!isStarted || !selectedProfile || gameOver || isThinking || status === 'error') {
      return;
    }

    const turnColor = game.turn() === 'w' ? 'white' : 'black';
    if (turnColor === botColor) {
      requestBotMove();
    }
  }, [botColor, game, gameOver, isStarted, isThinking, requestBotMove, selectedProfile, status]);

  function startGame() {
    const nextGame = new Chess();
    positionKeysRef.current = [positionKey(nextGame.fen())];
    setGame(nextGame);
    setIsStarted(true);
    setLastMove(null);
    setMessage(null);
    setStatus('ready');
    setSelectedSquare(null);
    setPremove(null);

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

  function resetGame() {
    positionKeysRef.current = [positionKey(START_FEN)];
    setGame(new Chess());
    setIsStarted(false);
    setLastMove(null);
    setMessage(null);
    setStatus('ready');
    setSelectedSquare(null);
    setPremove(null);
    botMoveInFlightRef.current = false;
    setIsThinking(false);
  }

  function tryMove(sourceSquare: string, targetSquare: string): boolean {
    if (!humanCanMove) {
      return false;
    }

    if (sourceSquare === targetSquare) {
      return false;
    }

    const nextGame = new Chess(gameRef.current.fen());
    let move: ReturnType<typeof nextGame.move> | null = null;
    try {
      move = nextGame.move({
        from: sourceSquare,
        to: targetSquare,
        promotion: 'q',
      });
    } catch {
      return false;
    }

    if (!move) {
      return false;
    }

    positionKeysRef.current.push(positionKey(nextGame.fen()));
    setGame(nextGame);
    setLastMove(null);
    setMessage(null);
    setStatus('ready');
    setSelectedSquare(null);
    return true;
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
      const clickedPiece = gameRef.current.get(square as Square);
      const isOwnPiece = clickedPiece?.color === gameRef.current.turn();

      if (!selectedSquare) {
        setSelectedSquare(isOwnPiece ? square : null);
        return;
      }

      if (selectedSquare === square) {
        setSelectedSquare(null);
        return;
      }

      const sourcePiece = gameRef.current.get(selectedSquare as Square);
      const legalMove = gameRef.current
        .moves({ square: selectedSquare as Square, verbose: true })
        .some((move) => move.to === square);

      if (legalMove && sourcePiece) {
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
    if (premove) {
      const premoveStyle = { backgroundColor: 'rgba(56, 189, 248, 0.4)' };
      squares[premove.from] = premoveStyle;
      squares[premove.to] = premoveStyle;
    }
    if (selectedSquare) {
      squares[selectedSquare] = { backgroundColor: 'rgba(255, 170, 0, 0.35)' };
    }
    return squares;
  }, [premove, selectedSquare]);

  const hintSquares = useMemo<Record<string, 'dot' | 'ring'>>(() => {
    const sourceGame = humanCanMove ? game : premoveProbeGame;
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
  }, [game, humanCanMove, premoveProbeGame, selectedSquare]);

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
    <div className="min-h-full w-full px-6 pb-3 pt-6 text-white xl:h-full xl:overflow-hidden lg:px-10 [background-image:url(/walnut-dark.webp)] [background-size:cover] [background-position:center]">
      <ReviewShell
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
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className="transition-transform duration-200 group-hover:translate-x-0.5" aria-hidden>
                <path d="M5 12h14" />
                <path d="m12 5 7 7-7 7" />
              </svg>
            </button>
          </aside>
        }
        boardPanel={
          <div className="relative mx-auto aspect-square w-full max-w-[calc(100svh-7rem)] xl:max-h-full">
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
                    position: game.fen() === new Chess().fen() ? START_FEN : game.fen(),
                    boardOrientation: humanColor,
                    allowDragging: humanCanMove || canPremove,
                    canDragPiece: ({ piece }) => {
                      if (humanCanMove) {
                        return piece.pieceType[0] === gameRef.current.turn();
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

            <StatusStrip
              status={status}
              isThinking={isThinking}
              lastMove={lastMove}
              message={message}
              gameOver={gameOver && isStarted}
              endingText={endingText}
              isStarted={isStarted}
              premoveSan={premoveSan}
              onReset={resetGame}
              canReset={isStarted}
            />
          </aside>
        }
      />
    </div>
  );
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
                  {opening.color === 'white' && (
                    <div className="mt-0.5 text-[11px] text-[#f7e5c6]/55">
                      Playing as White
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
                    {opening.color === 'white' && (
                      <span className="ml-1.5 text-[10px] font-normal text-[#f7e5c6]/45">
                        as White
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
  // remounts it and the state below starts from the new profile — no
  // set-state-in-effect reset.
  const [traps, setTraps] = useState<OpponentTrap[]>(profile?.traps ?? []);
  const [phase, setPhase] = useState<TrapsPhase>(
    provider && username ? 'checking' : 'idle'
  );
  const [progress, setProgress] = useState<{ analyzed: number; total: number } | null>(null);
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
          if (!cancelled) {
            setPhase('idle');
          }
          return;
        }
        const data = (await response.json()) as AnalysisStatusResponse;
        if (cancelled) {
          return;
        }
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
        if (!analysisComplete) {
          setProgress({ analyzed: data.analyzed_games, total: data.total_games });
          setPhase('polling');
          timer = setTimeout(pollAnalysis, ANALYSIS_POLL_INTERVAL_MS);
          return;
        }
        // Analysis finished — pull the traps it produced, then stop.
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

      {top.length === 0 ? (
        <div className="mt-3">
          {phase === 'checking' || phase === 'polling' ? (
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
                  ? 'Not enough game data yet for reliable traps.'
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
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
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
        {children}
      </div>
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
  const [phase, setPhase] = useState<'loading' | 'error' | 'ready'>('loading');
  const [positions, setPositions] = useState<string[]>([trap.fen]);
  const [moveIndex, setMoveIndex] = useState(0);
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
        // position before the error (ply p) is positions[p - 1].
        const replay = new Chess();
        replay.loadPgn(game.pgn);
        const stepper = new Chess();
        const nextPositions = [START_FEN];
        for (const san of replay.history()) {
          stepper.move(san);
          nextPositions.push(stepper.fen());
        }
        setPositions(nextPositions);
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
  const sideToMove: 'white' | 'black' =
    position.split(' ')[1] === 'b' ? 'black' : 'white';
  const navButtonClass =
    'flex h-7 w-7 items-center justify-center rounded-lg border border-[#f7e5c6]/20 bg-black/40 text-xs text-[#f7e5c6]/80 transition hover:bg-black/60 disabled:pointer-events-none disabled:opacity-35';

  async function copyFen() {
    try {
      await navigator.clipboard.writeText(position);
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

      <div
        className={`mt-3 overflow-hidden rounded-2xl border border-black/50 ${modalBoardWidthClass}`}
      >
        <div className="relative aspect-square w-full">
          <Chessboard
            options={{
              position,
              boardOrientation: erringSide,
              ...modalBoardOptions,
            }}
          />
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
        <div
          className={`flex items-center gap-1 transition-opacity ${
            phase === 'ready' ? '' : 'pointer-events-none opacity-40'
          }`}
        >
          <button
            type="button"
            onClick={() => setMoveIndex(0)}
            disabled={moveIndex === 0}
            aria-label="First position"
            className={navButtonClass}
          >
            «
          </button>
          <button
            type="button"
            onClick={() => setMoveIndex((current) => Math.max(0, current - 1))}
            disabled={moveIndex === 0}
            aria-label="Previous move"
            className={navButtonClass}
          >
            ‹
          </button>
          <button
            type="button"
            onClick={() =>
              setMoveIndex((current) => Math.min(lastMoveIndex, current + 1))
            }
            disabled={moveIndex >= lastMoveIndex}
            aria-label="Next move"
            className={navButtonClass}
          >
            ›
          </button>
          <button
            type="button"
            onClick={() => setMoveIndex(lastMoveIndex)}
            disabled={moveIndex >= lastMoveIndex}
            aria-label="Last position"
            className={navButtonClass}
          >
            »
          </button>
          <span className="ml-1 text-[11px] tabular-nums text-[#f7e5c6]/50">
            {phase === 'ready' ? `${moveIndex} / ${lastMoveIndex}` : '– / –'}
          </span>
        </div>
        <button
          type="button"
          onClick={copyFen}
          className="shrink-0 rounded-full border border-[#f7e5c6]/25 bg-black/45 px-3 py-1.5 text-[11px] font-semibold text-[#f7e5c6]/80 transition hover:bg-black/65"
        >
          {copied ? 'Copied' : 'Copy FEN'}
        </button>
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

  const goToGame = (nextIndex: number) => {
    if (nextIndex < 0 || nextIndex >= games.length || nextIndex === gameIndex) {
      return;
    }
    setGameIndex(nextIndex);
  };

  return (
    <ModalShell
      title={`${family} · as ${color === 'white' ? 'White' : 'Black'}`}
      onClose={onClose}
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
          re-imported since this row was built — refresh and try again.
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
                    position,
                    boardOrientation: color,
                    ...modalBoardOptions,
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

          {/* One control bar: game nav on the left (losses lead the list,
              newest first, so ‹ steps towards newer games) and move nav on
              the right, so the modal stays inside short viewports. The move
              group is dimmed until the selected game's PGN is replayed. */}
          <div className="mt-3 flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
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
            </div>
            <div
              className={`flex items-center gap-1 transition-opacity ${
                gamePhase === 'ready' && game
                  ? ''
                  : 'pointer-events-none opacity-40'
              }`}
            >
              <button
                type="button"
                onClick={() => setMoveIndex(0)}
                disabled={moveIndex === 0}
                aria-label="First position"
                className={navButtonClass}
              >
                «
              </button>
              <button
                type="button"
                onClick={() =>
                  setMoveIndex((current) => Math.max(0, current - 1))
                }
                disabled={moveIndex === 0}
                aria-label="Previous move"
                className={navButtonClass}
              >
                ‹
              </button>
              <button
                type="button"
                onClick={() =>
                  setMoveIndex((current) =>
                    Math.min(lastMoveIndex, current + 1)
                  )
                }
                disabled={moveIndex >= lastMoveIndex}
                aria-label="Next move"
                className={navButtonClass}
              >
                ›
              </button>
              <button
                type="button"
                onClick={() => setMoveIndex(lastMoveIndex)}
                disabled={moveIndex >= lastMoveIndex}
                aria-label="Last position"
                className={navButtonClass}
              >
                »
              </button>
              <span className="ml-1 text-[11px] tabular-nums text-[#f7e5c6]/50">
                {gamePhase === 'ready' && game
                  ? `${moveIndex} / ${lastMoveIndex}`
                  : '– / –'}
              </span>
            </div>
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
            <div className="mt-3 flex items-center justify-between gap-3 rounded-xl border border-black/30 bg-black/30 px-3 py-2">
              <span className="min-w-0 truncate text-[11px] text-[#f7e5c6]/70">
                First {summary.first_blunder.classification}: move{' '}
                {summary.first_blunder.move_number} ·{' '}
                {summary.first_blunder.move_san}
              </span>
              <button
                type="button"
                onClick={() =>
                  setMoveIndex(
                    Math.max(0, (summary.first_blunder?.ply ?? 1) - 1)
                  )
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

function StatusStrip({
  status,
  isThinking,
  lastMove,
  message,
  gameOver,
  endingText,
  isStarted,
  premoveSan,
  onReset,
  canReset,
}: {
  status: BotSource;
  isThinking: boolean;
  lastMove: SparringMoveResponse | null;
  message: string | null;
  gameOver: boolean;
  /** How the game ended (win/draw + rule); null falls back to "Finished". */
  endingText: string | null;
  isStarted: boolean;
  premoveSan: string | null;
  onReset: () => void;
  canReset: boolean;
}) {
  const label = sourceLabel(status);

  if (!isStarted && !message) {
    return null;
  }

  return (
    <div className="flex shrink-0 items-center justify-between gap-3 border-t border-black/40 bg-black/40 px-4 py-2.5">
      <div className="flex min-w-0 items-center gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[#f7e5c6]/55">
          {gameOver ? (endingText ?? 'Finished') : label}
        </span>
        {isThinking && (
          <span className="h-3 w-3 rounded-full border-2 border-[#f7e5c6]/35 border-t-[#f7e5c6] animate-spin" />
        )}
        {lastMove && !gameOver && (
          <span className="truncate text-[11px] text-[#f7e5c6]/65">
            {lastMove.move_san}
          </span>
        )}
        {premoveSan && !gameOver && (
          <span className="shrink-0 rounded-full border border-sky-400/30 bg-sky-400/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-sky-200">
            Premove {premoveSan}
          </span>
        )}
      </div>
      <div className="flex items-center gap-2">
        {message && (
          <span className="max-w-[12rem] truncate text-[11px] text-red-300" title={message}>
            {message}
          </span>
        )}
        {canReset && (
          <button
            type="button"
            onClick={onReset}
            className="h-7 rounded-full border border-[#f7e5c6]/25 bg-black/45 px-3 text-[11px] font-semibold text-[#f7e5c6]/80 transition hover:bg-black/65"
          >
            Reset
          </button>
        )}
      </div>
    </div>
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

function sourceLabel(status: BotSource) {
  if (status === 'in_book') return 'In book';
  if (status === 'playing_naturally') return 'Playing naturally';
  if (status === 'thinking') return 'Thinking';
  if (status === 'error') return 'Needs attention';
  return 'Ready';
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

function gameEndingLabel(ending: GameEnding, game: Chess): string {
  if (ending === 'checkmate') {
    const winner = game.turn() === 'w' ? 'Black' : 'White';
    return `Checkmate — ${winner} wins`;
  }
  if (ending === 'stalemate') return 'Draw — stalemate';
  if (ending === 'insufficient_material') return 'Draw — insufficient material';
  if (ending === 'fifty_move_rule') return 'Draw — fifty-move rule';
  return 'Draw — threefold repetition';
}

function uciToMove(uci: string) {
  return {
    from: uci.slice(0, 2),
    to: uci.slice(2, 4),
    promotion: uci.slice(4, 5) || undefined,
  };
}
