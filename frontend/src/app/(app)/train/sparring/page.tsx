'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Chess } from 'chess.js';
import { Chessboard } from 'react-chessboard';

type BotSource = 'ready' | 'in_book' | 'playing_naturally' | 'correcting_blunder' | 'thinking' | 'error';

type OpponentProfile = {
  provider: 'lichess' | 'chesscom';
  opponent_username: string;
  game_count: number;
  rating: number;
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

const START_FEN = 'start';
const woodPanelClass =
  'border border-black/50 [background-image:linear-gradient(rgba(0,0,0,0.56),rgba(0,0,0,0.56)),url(/walnut-dark.png)] [background-size:cover] [background-position:center] [box-shadow:0_10px_30px_rgba(0,0,0,0.48),inset_0_1px_0_rgba(255,255,255,0.06),inset_0_-1px_0_rgba(0,0,0,0.5)]';

export default function SparringPage() {
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
  const moveHistory = game.history({ verbose: true }).slice(-8).reverse();
  const gameOver = game.isGameOver();

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
          bot_color: botColor,
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
    if (!isStarted || !selectedProfile || gameOver || isThinking) {
      return;
    }

    const turnColor = game.turn() === 'w' ? 'white' : 'black';
    if (turnColor === botColor) {
      requestBotMove();
    }
  }, [botColor, game, gameOver, isStarted, isThinking, requestBotMove, selectedProfile]);

  function startGame() {
    const nextGame = new Chess();
    setGame(nextGame);
    setIsStarted(true);
    setLastMove(null);
    setMessage(null);
    setStatus('ready');
  }

  function resetGame() {
    setGame(new Chess());
    setIsStarted(false);
    setLastMove(null);
    setMessage(null);
    setStatus('ready');
    botMoveInFlightRef.current = false;
    setIsThinking(false);
  }

  function handleDrop({
    sourceSquare,
    targetSquare,
  }: {
    sourceSquare: string;
    targetSquare: string | null;
  }) {
    if (!targetSquare || !isStarted || isThinking || gameOver) {
      return false;
    }

    const turnColor = gameRef.current.turn() === 'w' ? 'white' : 'black';
    if (turnColor !== humanColor) {
      return false;
    }

    const nextGame = new Chess(gameRef.current.fen());
    const move = nextGame.move({
      from: sourceSquare,
      to: targetSquare,
      promotion: 'q',
    });

    if (!move) {
      return false;
    }

    setGame(nextGame);
    setLastMove(null);
    setMessage(null);
    return true;
  }

  return (
    <div className="relative -mt-2 min-h-[calc(100vh-2.25rem)] w-full overflow-y-auto px-4 py-6 text-white lg:px-8 [background-image:url(/walnut-dark.png)] [background-size:cover] [background-position:center]">
      <div className="mx-auto grid max-w-7xl gap-5 xl:grid-cols-[minmax(22rem,26rem)_minmax(24rem,1fr)_minmax(18rem,22rem)]">
        <section className={`${woodPanelClass} flex flex-col gap-4 rounded-[8px] p-5`}>
          <div>
            <h1 className="text-2xl font-semibold text-[#f7e5c6]">Sparring</h1>
            <p className="mt-1 text-sm text-[#f7e5c6]/60">
              {selectedProfile
                ? `${selectedProfile.game_count} imported games, estimated ${selectedProfile.rating}`
                : 'Import an opponent first.'}
            </p>
          </div>

          <label className="flex flex-col gap-2 text-sm font-medium text-[#f7e5c6]/85">
            Opponent
            <select
              value={selectedKey}
              onChange={(event) => setSelectedKey(event.target.value)}
              className="h-11 rounded-[8px] border border-black/50 bg-black/60 px-3 text-sm text-white outline-none transition focus:border-[#10b981]/60 focus:ring-2 focus:ring-[#10b981]/20"
            >
              {profiles.length === 0 ? (
                <option value="">No imported opponents</option>
              ) : (
                profiles.map((profile) => (
                  <option key={profileKey(profile)} value={profileKey(profile)}>
                    {profile.opponent_username} ({profile.provider})
                  </option>
                ))
              )}
            </select>
          </label>

          <div className="grid grid-cols-2 overflow-hidden rounded-[8px] border border-black/50 bg-black/50 p-1">
            {(['white', 'black'] as const).map((color) => (
              <button
                key={color}
                type="button"
                onClick={() => setHumanColor(color)}
                className={`h-10 rounded-[6px] text-sm font-semibold transition ${
                  humanColor === color
                    ? 'bg-[#f7e5c6] text-[#241206]'
                    : 'text-[#f7e5c6]/70 hover:bg-white/8'
                }`}
              >
                Play {capitalize(color)}
              </button>
            ))}
          </div>

          <div className="grid grid-cols-2 gap-3">
            <button
              type="button"
              onClick={startGame}
              disabled={!selectedProfile || isThinking}
              className="h-11 rounded-[8px] border border-[#10b981]/35 bg-[#10b981]/14 px-4 text-sm font-semibold text-[#a7f3d0] transition hover:bg-[#10b981]/20 disabled:pointer-events-none disabled:opacity-40"
            >
              Start
            </button>
            <button
              type="button"
              onClick={resetGame}
              className="h-11 rounded-[8px] border border-[#f7e5c6]/20 bg-black/35 px-4 text-sm font-semibold text-[#f7e5c6]/80 transition hover:bg-black/50"
            >
              Reset
            </button>
          </div>

          <StatusPanel
            status={status}
            isThinking={isThinking}
            lastMove={lastMove}
            message={message ?? loadError}
          />
        </section>

        <section className="flex items-start justify-center">
          <div className={`${woodPanelClass} aspect-square w-full max-w-[min(82vh,760px)] rounded-[8px] p-3`}>
            <div className="relative h-full w-full">
              <div
                style={{
                  position: 'absolute',
                  inset: 0,
                  backgroundImage: 'url("/wood-texture.png")',
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
                  allowDragging: isStarted && !isThinking && !gameOver,
                  canDragPiece: ({ piece }) => {
                    if (!isStarted || isThinking || gameOver) {
                      return false;
                    }
                    const turnColor = gameRef.current.turn() === 'w' ? 'white' : 'black';
                    return turnColor === humanColor && piece.pieceType[0] === gameRef.current.turn();
                  },
                  onPieceDrop: handleDrop,
                  boardStyle: {
                    width: '100%',
                    height: '100%',
                    borderRadius: '8px',
                    boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
                  },
                  darkSquareStyle: {
                    backgroundImage: 'url(/walnut-dark.png)',
                    backgroundSize: '110% 110%',
                    backgroundPosition: 'center',
                  },
                  lightSquareStyle: {
                    backgroundImage: 'url(/walnut-light.png)',
                    backgroundSize: '110% 110%',
                    backgroundPosition: 'center',
                  },
                  darkSquareNotationStyle: { color: '#f0e0c0' },
                  lightSquareNotationStyle: { color: '#3a2410' },
                  animationDurationInMs: 180,
                }}
              />
            </div>
          </div>
        </section>

        <aside className={`${woodPanelClass} flex flex-col gap-4 rounded-[8px] p-5`}>
          <div className="flex items-center justify-between gap-3">
            <h2 className="text-sm font-semibold uppercase tracking-[0.14em] text-[#f7e5c6]/70">
              Moves
            </h2>
            <span className="text-xs text-[#f7e5c6]/50">
              {gameOver ? 'Finished' : `${capitalize(game.turn() === 'w' ? 'white' : 'black')} to move`}
            </span>
          </div>

          <div className="flex min-h-72 flex-col gap-2 overflow-y-auto pr-1">
            {moveHistory.length === 0 ? (
              <div className="rounded-[8px] border border-black/40 bg-black/30 px-3 py-8 text-center text-sm text-[#f7e5c6]/55">
                Start a session to see the move log.
              </div>
            ) : (
              moveHistory.map((move, index) => (
                <div
                  key={`${move.before}-${move.after}-${index}`}
                  className="grid grid-cols-[1fr_auto] items-center gap-3 rounded-[8px] bg-black/30 px-3 py-2 text-sm"
                >
                  <span className="font-semibold text-[#f7e5c6]">{move.san}</span>
                  <span className="text-xs uppercase text-[#f7e5c6]/45">{move.color}</span>
                </div>
              ))
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}

function StatusPanel({
  status,
  isThinking,
  lastMove,
  message,
}: {
  status: BotSource;
  isThinking: boolean;
  lastMove: SparringMoveResponse | null;
  message: string | null;
}) {
  const label = sourceLabel(status);
  return (
    <div className="rounded-[8px] border border-black/45 bg-black/35 p-4">
      <div className="flex items-center justify-between gap-3">
        <span className="text-sm font-semibold text-[#f7e5c6]">{label}</span>
        {isThinking && (
          <span className="h-4 w-4 rounded-full border-2 border-[#f7e5c6]/35 border-t-[#f7e5c6] animate-spin" />
        )}
      </div>
      {lastMove && (
        <div className="mt-3 space-y-1 text-sm text-[#f7e5c6]/65">
          <div>
            Last move <span className="font-semibold text-[#f7e5c6]">{lastMove.move_san}</span>
          </div>
          <div>
            {lastMove.source === 'in_book' && lastMove.repertoire_frequency
              ? `Seen ${lastMove.repertoire_frequency} time${lastMove.repertoire_frequency === 1 ? '' : 's'}`
              : `Maia Elo ${lastMove.opponent_elo}`}
          </div>
          {lastMove.source === 'correcting_blunder' && (
            <div className="text-amber-200">
              Avoided a {lastMove.cp_loss} cp drop; Stockfish preferred {lastMove.best_move_san ?? 'the played move'}.
            </div>
          )}
        </div>
      )}
      {message && (
        <div className="mt-3 rounded-[8px] border border-red-400/30 bg-red-400/10 px-3 py-2 text-sm text-red-200">
          {message}
        </div>
      )}
    </div>
  );
}

function profileKey(profile: OpponentProfile | undefined) {
  if (!profile) {
    return '';
  }
  return `${profile.provider}:${profile.opponent_username}`;
}

function sourceLabel(status: BotSource) {
  if (status === 'in_book') return 'In book';
  if (status === 'playing_naturally') return 'Playing naturally';
  if (status === 'correcting_blunder') return 'Correcting a blunder';
  if (status === 'thinking') return 'Thinking';
  if (status === 'error') return 'Needs attention';
  return 'Ready';
}

function capitalize(value: string) {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

function uciToMove(uci: string) {
  return {
    from: uci.slice(0, 2),
    to: uci.slice(2, 4),
    promotion: uci.slice(4, 5) || undefined,
  };
}
