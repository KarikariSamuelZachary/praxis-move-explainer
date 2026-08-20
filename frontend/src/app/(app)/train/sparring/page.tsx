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
  | 'correcting_blunder'
  | 'thinking'
  | 'error';

type TimeClassKey = 'rapid' | 'blitz' | 'bullet' | 'classical' | 'daily';

type OpponentProfile = {
  provider: 'lichess' | 'chesscom';
  opponent_username: string;
  game_count: number;
  rating: number;
  ratings_by_time_class: Partial<Record<TimeClassKey, number>> | null;
  playing_style: 'Passive' | 'Balanced' | 'Aggressive' | null;
  preferred_time_control: string | null;
  time_control_distribution: Record<string, number> | null;
  opening_results: Record<string, unknown> | null;
  openings_lost_against: {
    name: string;
    loss_percentage: number;
    games: number;
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
  tier: 'position';
};

type OpponentProfileInfo = {
  provider: 'lichess' | 'chesscom';
  opponent_username: string;
  avatar_url: string | null;
  verified: boolean;
};

type SparringMoveResponse = {
  move_uci: string;
  move_san: string;
  source: 'in_book' | 'playing_naturally' | 'correcting_blunder';
  opponent_elo: number;
  repertoire_frequency?: number | null;
  cp_loss: number;
  best_move_san?: string | null;
};

type ApiErrorResponse = {
  detail?: string;
  error?: string;
};

const START_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';

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
  { label: string; icon: React.ReactNode; tone: string }
> = {
  rapid: {
    label: 'Rapid',
    tone: 'text-emerald-300',
    icon: (
      <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
        <path d="M12 2 4 5v6c0 4.5 3.4 8.7 8 11 4.6-2.3 8-6.5 8-11V5l-8-3Zm0 4.3 5 1.9V11c0 3.1-2.2 6.1-5 7.7-2.8-1.6-5-4.6-5-7.7V8.2l5-1.9Z" />
      </svg>
    ),
  },
  blitz: {
    label: 'Blitz',
    tone: 'text-amber-300',
    icon: (
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <path d="M13 2 3 14h7l-1 8 11-14h-7l1-6Z" />
      </svg>
    ),
  },
  bullet: {
    label: 'Bullet',
    tone: 'text-violet-300',
    icon: (
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <path d="M4.5 16.5c-1.5 1.3-2 3.2-1 4.2s2.9.5 4.2-1c1.4-1.5 3.5-3.5 3.5-7.7L13 8l3-1-1 3-3 3c0 4-2 6-4.5 7.5Z" />
        <path d="M14 12 21 5l-2-2-7 7" />
      </svg>
    ),
  },
  classical: {
    label: 'Classical',
    tone: 'text-sky-300',
    icon: (
      <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
        <path d="M12 2 4 5v6c0 4.5 3.4 8.7 8 11 4.6-2.3 8-6.5 8-11V5l-8-3Z" />
      </svg>
    ),
  },
  daily: {
    label: 'Daily',
    tone: 'text-stone-300',
    icon: (
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <rect x="3" y="4" width="18" height="17" rx="2" />
        <path d="M3 9h18" />
        <path d="M8 2v4" />
        <path d="M16 2v4" />
      </svg>
    ),
  },
};

const TIME_CLASS_ORDER: TimeClassKey[] = ['rapid', 'blitz', 'bullet'];

export default function SparringPage() {
  const [profiles, setProfiles] = useState<OpponentProfile[]>([]);
  const [selectedKey, setSelectedKey] = useState('');
  // Per the mockup spec: the user is always white, the bot always black.
  // The "Play As" toggle was intentionally omitted from the design; this
  // removes that UI but preserves the same gameplay semantics by pinning
  // the color choice at the page level (avoids needing a stale
  // humanColor toggle UI hanging around after Start).
  const [humanColor] = useState<'white' | 'black'>('white');
  const [game, setGame] = useState(() => new Chess());
  const [isStarted, setIsStarted] = useState(false);
  const [isThinking, setIsThinking] = useState(false);
  const [status, setStatus] = useState<BotSource>('ready');
  const [message, setMessage] = useState<string | null>(null);
  const [lastMove, setLastMove] = useState<SparringMoveResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selectedSquare, setSelectedSquare] = useState<string | null>(null);
  const [timeControl, setTimeControl] = useState<string>('');
  const [profileInfo, setProfileInfo] = useState<OpponentProfileInfo | null>(null);
  const gameRef = useRef(game);
  const botMoveInFlightRef = useRef(false);

  useEffect(() => {
    gameRef.current = game;
  }, [game]);

  useEffect(() => {
    let isCancelled = false;

    async function loadOpponents() {
      try {
        const response = await fetch('/api/train/opponents', { cache: 'no-store' });
        if (!response.ok) {
          const body = (await response.json().catch(() => ({}))) as ApiErrorResponse;
          throw new Error(body.detail ?? body.error ?? `Opponent request failed (${response.status})`);
        }
        const data = (await response.json()) as { opponents: OpponentProfile[] };
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

  const selectedProfile = useMemo(
    () => profiles.find((profile) => profileKey(profile) === selectedKey) ?? null,
    [profiles, selectedKey]
  );
  const botColor = humanColor === 'white' ? 'black' : 'white';

  // Lazy-fetch the opponent's display metadata (avatar + verified badge)
  // when the selected profile changes. Server-side TTL-cached at the
  // chess.com profile endpoint; lichess returns {avatar_url: null,
  // verified: false} without a network call. The fetch failure is silent
  // — the frontend falls back to an initials avatar without a check.
  useEffect(() => {
    if (!selectedProfile) {
      setProfileInfo(null);
      return;
    }
    let isCancelled = false;

    async function loadProfileInfo() {
      try {
        const params = new URLSearchParams({
          provider: selectedProfile!.provider,
          opponent_username: selectedProfile!.opponent_username,
        });
        const response = await fetch(
          `/api/train/opponent-profile-info?${params.toString()}`,
          { cache: 'no-store' }
        );
        if (!response.ok) return;
        const data = (await response.json()) as OpponentProfileInfo;
        if (!isCancelled) setProfileInfo(data);
      } catch {
        // Silent — fall back to initials avatar without a check.
      }
    }

    loadProfileInfo();
    return () => {
      isCancelled = true;
    };
  }, [selectedProfile]);

  // Prefill the Time Control dropdown when the selected profile changes.
  // Uses preferred_time_control when present (the recency-weighted most-
  // common bucket); falls back to the first key of the distribution when
  // only the distribution is available. Empty string leaves the dropdown
  // unselected (the mockup's "—" placeholder).
  useEffect(() => {
    if (selectedProfile?.preferred_time_control) {
      setTimeControl(selectedProfile.preferred_time_control);
    } else if (selectedProfile?.time_control_distribution) {
      const first = Object.keys(selectedProfile.time_control_distribution)[0];
      setTimeControl(first ?? '');
    } else {
      setTimeControl('');
    }
  }, [selectedProfile]);

  // The right card no longer shows a per-move log — the mockup replaces
// it with three aggregate sections (Openings / Traps / Time Control).
// The latest bot move's SAN still surfaces in the StatusStrip via the
// `lastMove` API response, so we don't need a derived `moveHistory`
// here.
const gameOver = game.isGameOver();

  const humanCanMove =
    isStarted &&
    !isThinking &&
    !gameOver &&
    (game.turn() === 'w' ? 'white' : 'black') === humanColor;

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
  }, [botColor, selectedProfile]);

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
    setGame(nextGame);
    setIsStarted(true);
    setLastMove(null);
    setMessage(null);
    setStatus('ready');
    setSelectedSquare(null);
  }

  function resetGame() {
    setGame(new Chess());
    setIsStarted(false);
    setLastMove(null);
    setMessage(null);
    setStatus('ready');
    setSelectedSquare(null);
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

    setGame(nextGame);
    setLastMove(null);
    setMessage(null);
    setStatus('ready');
    setSelectedSquare(null);
    return true;
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
    return tryMove(sourceSquare, targetSquare);
  }

  function handleSquareClick({ square }: { piece: { pieceType: string } | null; square: string }) {
    if (!humanCanMove) {
      setSelectedSquare(null);
      return;
    }

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
  }

  const highlightSquares = useMemo<Record<string, React.CSSProperties>>(() => {
    if (!selectedSquare) return {};
    return {
      [selectedSquare]: { backgroundColor: 'rgba(255, 170, 0, 0.35)' },
    };
  }, [selectedSquare]);

  const hintSquares = useMemo<Record<string, 'dot' | 'ring'>>(() => {
    if (!selectedSquare) return {};
    try {
      const legalMoves = game.moves({ square: selectedSquare as Square, verbose: true });
      const hints: Record<string, 'dot' | 'ring'> = {};
      for (const move of legalMoves) {
        const targetPiece = game.get(move.to as Square);
        hints[move.to] = targetPiece ? 'ring' : 'dot';
      }
      return hints;
    } catch {
      return {};
    }
  }, [game, selectedSquare]);

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

  // Time-control options for the dropdown. Built from the selected
  // profile's distribution so the dropdown never offers buckets the
  // opponent doesn't actually play. Empty when no profile is selected.
  const timeControlOptions = useMemo<string[]>(() => {
    if (selectedProfile?.time_control_distribution) {
      return Object.keys(selectedProfile.time_control_distribution);
    }
    if (selectedProfile?.preferred_time_control) {
      return [selectedProfile.preferred_time_control];
    }
    return [];
  }, [selectedProfile]);

  return (
    <div className="relative -mt-2 h-[calc(100vh-2.5rem)] w-full overflow-y-auto px-6 pb-[10px] pt-6 text-white lg:overflow-hidden lg:px-10 [background-image:url(/walnut-dark.webp)] [background-size:cover] [background-position:center]">
      <ReviewShell
        importPanel={
          <aside className={panelClass}>
            <ProfileCard
              profile={selectedProfile}
              profileInfo={profileInfo}
              gameCount={selectedProfile?.game_count ?? 0}
              estimatedRating={selectedProfile?.rating ?? null}
              opponents={profiles}
              selectedKey={selectedKey}
              onSelectKey={setSelectedKey}
              loadError={loadError}
            />

            <RatingsRow ratings={selectedProfile?.ratings_by_time_class ?? null} />

            <PlayingStylePill style={selectedProfile?.playing_style ?? null} />

            <TimeControlSelect
              options={timeControlOptions}
              value={timeControl}
              onChange={setTimeControl}
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
                    position: game.fen() === new Chess().fen() ? START_FEN : game.fen(),
                    boardOrientation: humanColor,
                    allowDragging: humanCanMove,
                    canDragPiece: ({ piece }) => {
                      if (!humanCanMove) {
                        return false;
                      }
                      return piece.pieceType[0] === gameRef.current.turn();
                    },
                    onPieceDrop: handleDrop,
                    onSquareClick: handleSquareClick,
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
            <div className="wooden-scroll flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4">
              <OpeningsLostAgainst openings={selectedProfile?.openings_lost_against ?? []} />

              <TrapsFallenFor traps={selectedProfile?.traps ?? []} />

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
              isStarted={isStarted}
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
  profileInfo,
  gameCount,
  estimatedRating,
  opponents,
  selectedKey,
  onSelectKey,
  loadError,
}: {
  profile: OpponentProfile | null;
  profileInfo: OpponentProfileInfo | null;
  gameCount: number;
  estimatedRating: number | null;
  opponents: OpponentProfile[];
  selectedKey: string;
  onSelectKey: (key: string) => void;
  loadError: string | null;
}) {
  const displayName = profile?.opponent_username ?? 'No opponent';
  const handle = profile ? `@${profile.opponent_username}` : '@—';
  const providerLabel = profile ? displayProvider(profile.provider) : '—';
  const initials = (profile?.opponent_username ?? '—')
    .replace(/[^A-Za-z0-9]/g, '')
    .slice(0, 2)
    .toUpperCase();
  const showVerified = profileInfo?.verified ?? false;
  const avatarUrl = profileInfo?.avatar_url ?? null;

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
          <div className="mt-0.5 flex items-center gap-1.5 text-xs text-[#f7e5c6]/55">
            <span className="truncate">{handle}</span>
            <span className="shrink-0 opacity-40">·</span>
            <span className="shrink-0">{providerLabel}</span>
            {estimatedRating !== null && (
              <>
                <span className="shrink-0 opacity-40">·</span>
                <span className="shrink-0">est. {estimatedRating}</span>
              </>
            )}
          </div>
        </div>
      </div>

      <select
        value={selectedKey}
        onChange={(event) => onSelectKey(event.target.value)}
        aria-label="Select opponent"
        className="h-10 cursor-pointer rounded-2xl border border-black/50 bg-black/60 px-3 text-sm text-white outline-none transition hover:border-emerald-400/30 focus:border-[#10b981]/60 focus:ring-2 focus:ring-[#10b981]/20"
      >
        {opponents.length === 0 ? (
          <option value="">No imported opponents</option>
        ) : (
          opponents.map((opp) => (
            <option key={profileKey(opp)} value={profileKey(opp)}>
              {opp.opponent_username} ({displayProvider(opp.provider)})
            </option>
          ))
        )}
      </select>

      {loadError && (
        <p role="alert" className="rounded-2xl border border-red-400/30 bg-red-400/10 px-3 py-2 text-xs text-red-300">
          {loadError}
        </p>
      )}

      {profile && gameCount === 0 && (
        <p className="text-[11px] text-[#f7e5c6]/55">No games imported for this opponent.</p>
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
        <div className="col-span-3 rounded-2xl border border-black/30 bg-black/25 px-3 py-2 text-center text-[11px] text-[#f7e5c6]/45">
          No rating data yet.
        </div>
      ) : (
        entries.map(({ key, value, meta }) => (
          <div
            key={key}
            className="flex cursor-default flex-col items-center gap-1 rounded-2xl border border-black/30 bg-black/30 px-2 py-2.5 transition-colors duration-200 hover:border-emerald-400/30 hover:bg-emerald-400/[0.06]"
          >
            <div className={meta.tone}>{meta.icon}</div>
            <div className="text-[10px] font-semibold uppercase tracking-wider text-[#f7e5c6]/55">
              {meta.label}
            </div>
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
        <span className="text-xs text-[#f7e5c6]/40">—</span>
      )}
    </section>
  );
}

function TimeControlSelect({
  options,
  value,
  onChange,
  disabled,
}: {
  options: string[];
  value: string;
  onChange: (next: string) => void;
  disabled: boolean;
}) {
  return (
    <section className="flex flex-col gap-1.5">
      <label className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[#f7e5c6]/55">
        Time Control
      </label>
      <div className="relative">
        <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[#f7e5c6]/60">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <circle cx="12" cy="12" r="9" />
            <path d="M12 7v5l3 2" />
          </svg>
        </span>
        <select
          value={value}
          onChange={(event) => onChange(event.target.value)}
          disabled={disabled}
          className="h-11 w-full cursor-pointer appearance-none rounded-2xl border border-black/50 bg-black/60 pl-9 pr-9 text-sm text-white outline-none transition hover:border-emerald-400/30 focus:border-[#10b981]/60 focus:ring-2 focus:ring-[#10b981]/20 disabled:pointer-events-none disabled:opacity-50"
        >
          {options.length === 0 ? (
            <option value="">—</option>
          ) : (
            options.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))
          )}
        </select>
        <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-[#f7e5c6]/60">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <path d="m6 9 6 6 6-6" />
          </svg>
        </span>
      </div>
    </section>
  );
}

function OpeningsLostAgainst({
  openings,
}: {
  openings: { name: string; loss_percentage: number; games: number }[];
}) {
  // Top 5 by descending loss% — the API already sorts this way, but
  // re-sort defensively in case a caller ever hand-builds the array.
  const top = [...openings]
    .sort((a, b) => b.loss_percentage - a.loss_percentage)
    .slice(0, 5);

  return (
    <section className="flex flex-col gap-2">
      <SectionHeader icon={<BookIcon />} title="Openings He Lost Against" />

      {top.length === 0 ? (
        <EmptyHint text="No decisive opening data yet." />
      ) : (
        <div className="flex flex-col gap-1.5">
          {top.map((opening) => (
            <div
              key={opening.name}
              className="grid grid-cols-[1fr_auto] items-center gap-3 rounded-lg px-1.5 py-1 -mx-1.5 text-xs transition-colors duration-200 hover:bg-white/[0.04]"
            >
              <div className="flex min-w-0 items-center gap-2">
                <span className="truncate text-[#f7e5c6]/85">{opening.name}</span>
                <div className="relative h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-black/45">
                  <div
                    className="absolute inset-y-0 left-0 rounded-full bg-emerald-400/85 transition-[width] duration-500 ease-out"
                    style={{
                      width: `${Math.max(2, Math.min(100, opening.loss_percentage * 100))}%`,
                    }}
                  />
                </div>
              </div>
              <span className="shrink-0 tabular-nums text-[#f7e5c6]">
                {Math.round(opening.loss_percentage * 100)}%
              </span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function TrapsFallenFor({ traps }: { traps: OpponentTrap[] }) {
  const top = traps.slice(0, 5);

  return (
    <section className="flex flex-col gap-2">
      <SectionHeader icon={<KnightIcon />} title="Traps He's Fallen For" />

      {top.length === 0 ? (
        <EmptyHint text="No recurring traps detected yet." />
      ) : (
        <div className="flex flex-col gap-1.5">
          {top.map((trap) => {
            const moveLabel = trap.moves.length > 0 ? trap.moves[0] : '?';
            const moveRange =
              trap.move_number_min === trap.move_number_max
                ? `move ${trap.move_number_min}`
                : `moves ${trap.move_number_min}-${trap.move_number_max}`;
            const games = `${trap.game_count} game${trap.game_count === 1 ? '' : 's'}`;
            const classificationTone =
              trap.classification === 'blunder'
                ? 'text-rose-300/90'
                : 'text-orange-300/90';

            return (
              <div
                key={trap.position_key}
                className="flex cursor-default items-center gap-2.5 rounded-2xl border border-black/30 bg-black/25 px-3 py-2 text-xs transition-colors duration-200 hover:border-emerald-400/25 hover:bg-black/40"
              >
                <div className={classificationTone}>
                  <KnightIcon />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[#f7e5c6]">
                    Played {moveLabel}
                  </div>
                  <div className="mt-0.5 text-[11px] text-[#f7e5c6]/50">
                    {trap.classification} · {moveRange}
                  </div>
                </div>
                <span className="shrink-0 text-[#f7e5c6]/75 tabular-nums">{games}</span>
              </div>
            );
          })}
        </div>
      )}
    </section>
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
    ? Object.entries(distribution).map(([label, fraction]) => ({
        label,
        fraction,
        color: TC_COLOR[label] ?? DEFAULT_TC_COLOR,
      }))
    : [];

  return (
    <section className="flex flex-col gap-2">
      <SectionHeader icon={<StopwatchIcon />} title="Preferred Time Control" />

      {entries.length === 0 ? (
        <EmptyHint text="No time-control data yet." />
      ) : (
        <div className="grid grid-cols-[110px_1fr] items-center gap-3 rounded-2xl border border-black/30 bg-black/25 px-3 py-3">
          <DonutChart entries={entries} />
          <div className="flex flex-col gap-1.5 text-xs">
            {entries.map(({ label, fraction, color }) => (
              <div
                key={label}
                className="grid grid-cols-[auto_1fr_auto] items-center gap-2 rounded-md px-1 -mx-1 transition-colors duration-200 hover:bg-white/[0.04]"
              >
                <span
                  aria-hidden
                  className="h-2.5 w-2.5 rounded-sm"
                  style={{ backgroundColor: color }}
                />
                <span className="text-[#f7e5c6]/85">{label}</span>
                <span className="tabular-nums text-[#f7e5c6]">
                  {Math.round(fraction * 100)}%
                </span>
              </div>
            ))}
            {mostPlayed && (
              <p className="mt-1 border-t border-white/5 pt-1.5 text-[11px] text-[#f7e5c6]/55">
                Most Played: <span className="text-[#f7e5c6]/80">{mostPlayed}</span>
              </p>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

function DonutChart({
  entries,
}: {
  entries: { label: string; fraction: number; color: string }[];
}) {
  // Donut geometry: SVG viewBox is 100x100, donut centered at (50,50),
  // outer radius 35, inner radius 22. Slices are drawn as filled donut
  // sectors; each slice's start/end are the cumulative fraction in [0, 1]
  // multiplied by 2*PI. Computed via reduce (NOT a let/mutate loop) so
  // the linter doesn't flag a render-phase mutation on the input array.
  const cx = 50;
  const cy = 50;
  const outerR = 35;
  const innerR = 22;

  const slices = entries.reduce<
    { label: string; fraction: number; color: string; start: number; end: number }[]
  >((acc, entry) => {
    const start = acc.length === 0 ? 0 : acc[acc.length - 1].end;
    acc.push({ ...entry, start, end: start + entry.fraction });
    return acc;
  }, []);

  return (
    <svg viewBox="0 0 100 100" className="block h-[100px] w-[100px]">
      {slices.map((slice) => {
        // Empty slice (fraction 0) — skip; an arc with start == end
        // collapses to a single point and renders nothing visible.
        if (slice.fraction <= 0) return null;
        // If the slice fills the whole circle (rare but possible when
        // one bucket is 100%), draw a full ring instead of a degenerate
        // two-arc path.
        if (slice.fraction >= 1) {
          return (
            <circle
              key={slice.label}
              cx={cx}
              cy={cy}
              r={(outerR + innerR) / 2}
              fill="none"
              stroke={slice.color}
              strokeWidth={outerR - innerR}
            />
          );
        }

        const startAngle = slice.start * 2 * Math.PI - Math.PI / 2;
        const endAngle = slice.end * 2 * Math.PI - Math.PI / 2;
        const largeArc = slice.fraction > 0.5 ? 1 : 0;

        const x1 = cx + outerR * Math.cos(startAngle);
        const y1 = cy + outerR * Math.sin(startAngle);
        const x2 = cx + outerR * Math.cos(endAngle);
        const y2 = cy + outerR * Math.sin(endAngle);
        const x3 = cx + innerR * Math.cos(endAngle);
        const y3 = cy + innerR * Math.sin(endAngle);
        const x4 = cx + innerR * Math.cos(startAngle);
        const y4 = cy + innerR * Math.sin(startAngle);

        const d = [
          `M ${x1} ${y1}`,
          `A ${outerR} ${outerR} 0 ${largeArc} 1 ${x2} ${y2}`,
          `L ${x3} ${y3}`,
          `A ${innerR} ${innerR} 0 ${largeArc} 0 ${x4} ${y4}`,
          'Z',
        ].join(' ');

        return <path key={slice.label} d={d} fill={slice.color} className="transition-opacity duration-200 hover:opacity-80" style={{ cursor: 'default' }} />;
      })}
    </svg>
  );
}

function StatusStrip({
  status,
  isThinking,
  lastMove,
  message,
  gameOver,
  isStarted,
  onReset,
  canReset,
}: {
  status: BotSource;
  isThinking: boolean;
  lastMove: SparringMoveResponse | null;
  message: string | null;
  gameOver: boolean;
  isStarted: boolean;
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
          {gameOver ? 'Finished' : label}
        </span>
        {isThinking && (
          <span className="h-3 w-3 rounded-full border-2 border-[#f7e5c6]/35 border-t-[#f7e5c6] animate-spin" />
        )}
        {lastMove && !gameOver && (
          <span className="truncate text-[11px] text-[#f7e5c6]/65">
            {lastMove.move_san}
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

function SectionHeader({ icon, title }: { icon: React.ReactNode; title: string }) {
  return (
    <div className="flex items-center gap-2 border-b border-white/5 pb-2 text-[#f7e5c6]">
      <span className="text-emerald-300/75">{icon}</span>
      <h3 className="text-[11px] font-semibold uppercase tracking-[0.22em]">
        {title}
      </h3>
    </div>
  );
}

function EmptyHint({ text }: { text: string }) {
  return (
    <div className="rounded-2xl border border-dashed border-[#f7e5c6]/15 bg-black/20 px-3 py-3 text-center text-[11px] text-[#f7e5c6]/45">
      {text}
    </div>
  );
}

function BookIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M4 4h12a4 4 0 0 1 4 4v12H8a4 4 0 0 1-4-4V4Z" />
      <path d="M4 16a4 4 0 0 1 4-4h12" />
    </svg>
  );
}

function KnightIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
      <path d="M5 21h14v-1H5v1Zm0-2h14a3 3 0 0 0-1.5-2.6c-.8-.5-1.3-.7-1.3-1.4 0-.6.4-.9.4-2.3 0-2-1-3.7-2.4-4.5-.4-.3-.7-.7-.7-1.2 0-1.7 2.4-2.3 2.4-4.4C16 0 13 0 12 1.4c-.3.5-.5 1-.5 1.6 0 .5.1.7.1 1.1 0 .4-.3.6-.6.6-.5 0-.7-.4-.7-.9 0-.4.1-.8.1-1.2 0-.6-.2-1-.6-1.4-.3-.3-.6-.4-1-.4-.6 0-1 .5-1 1.2 0 .4.2.7.5 1 .4.4.5.7.5 1.2 0 .6-.4 1-.9 1.3-1 .6-1.5 1.5-1.5 2.7 0 1.2.5 2.1 1.2 2.6.4.3.5.7.5 1.1 0 .7-.4 1.4-.6 2-.4 1.2-.4 2.3-.4 2.3l.6.6c-.4.4-.6.7-.6 1.2 0 .4.1.7.3 1Z" />
    </svg>
  );
}

function StopwatchIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <circle cx="12" cy="14" r="8" />
      <path d="M12 14V10" />
      <path d="M9 2h6" />
      <path d="m17 5 3-3" />
    </svg>
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
  if (status === 'correcting_blunder') return 'Correcting a blunder';
  if (status === 'thinking') return 'Thinking';
  if (status === 'error') return 'Needs attention';
  return 'Ready';
}

function uciToMove(uci: string) {
  return {
    from: uci.slice(0, 2),
    to: uci.slice(2, 4),
    promotion: uci.slice(4, 5) || undefined,
  };
}