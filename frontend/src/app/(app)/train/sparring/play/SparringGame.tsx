'use client';

import dynamic from 'next/dynamic';
import Image from 'next/image';
import Link from 'next/link';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Chess, Square } from 'chess.js';
import type { SquareRenderer } from 'react-chessboard';

import {
  PERSONAS,
  TIERS,
  randomEloInRange,
  type PersonaKey,
  type PlayColor,
  type TierKey,
} from '../personas';

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

type EngineSparringMoveResponse = {
  move_uci: string;
  move_san: string;
  persona: PersonaKey;
  engine_score_cp: number;
  engine_norm_cp: number;
  persona_final_cp: number;
  best_move_uci?: string | null;
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

export default function SparringGame({
  personaKey,
  tierKey,
  color,
}: {
  personaKey: PersonaKey;
  tierKey: TierKey;
  color: PlayColor;
}) {
  const persona = PERSONAS.find((entry) => entry.key === personaKey) ?? PERSONAS[0];
  const tier = TIERS.find((entry) => entry.key === tierKey) ?? TIERS[0];

  const [game, setGame] = useState(() => new Chess());
  const [botElo, setBotElo] = useState(() =>
    randomEloInRange(tier.minElo, tier.maxElo)
  );
  const [isThinking, setIsThinking] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [canRetry, setCanRetry] = useState(false);
  const [lastMoveSan, setLastMoveSan] = useState<string | null>(null);
  const [selectedSquare, setSelectedSquare] = useState<string | null>(null);

  const gameRef = useRef(game);
  const botMoveInFlightRef = useRef(false);

  useEffect(() => {
    gameRef.current = game;
  }, [game]);

  const botColor: PlayColor = color === 'white' ? 'black' : 'white';
  const gameOver = game.isGameOver();
  const resultText = useMemo(() => describeGameEnd(game), [game]);

  // A refresh/reload mid-game loses the board position AND re-picks the
  // bot's Elo (the game is client-owned and deliberately unpersisted), so
  // warn before unload while a game is actually in progress.
  const isGameInProgress = !gameOver && game.history().length > 0;

  useEffect(() => {
    if (!isGameInProgress) {
      return;
    }

    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };

    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => {
      window.removeEventListener('beforeunload', handleBeforeUnload);
    };
  }, [isGameInProgress]);

  const humanCanMove =
    !isThinking &&
    !gameOver &&
    (game.turn() === 'w' ? 'white' : 'black') === color;

  const requestBotMove = useCallback(async () => {
    if (botMoveInFlightRef.current || gameRef.current.isGameOver()) {
      return;
    }

    botMoveInFlightRef.current = true;
    setIsThinking(true);
    setMessage(null);
    setCanRetry(false);

    try {
      const response = await fetch('/api/train/engine-sparring-move', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          fen: gameRef.current.fen(),
          bot_color: botColor,
          persona: personaKey,
          target_elo: botElo,
        }),
      });

      if (!response.ok) {
        const body = (await response.json().catch(() => ({}))) as ApiErrorResponse;
        const detail = body.detail ?? body.error;
        let errorMessage = detail ?? `Move request failed (${response.status})`;
        let retryable = true;

        if (response.status === 429) {
          errorMessage = 'Rate limited. Wait a few seconds, then retry.';
        } else if (response.status === 409 && /game is (already )?over/i.test(detail ?? '')) {
          retryable = false;
        }

        const error = new Error(errorMessage) as Error & { retryable?: boolean };
        error.retryable = retryable;
        throw error;
      }

      const data = (await response.json()) as EngineSparringMoveResponse;
      const nextGame = new Chess(gameRef.current.fen());
      nextGame.move(uciToMove(data.move_uci));
      setGame(nextGame);
      setLastMoveSan(data.move_san);
    } catch (error) {
      setMessage(
        error instanceof Error ? error.message : 'Failed to get a sparring move.'
      );
      setCanRetry((error as { retryable?: boolean })?.retryable !== false);
    } finally {
      botMoveInFlightRef.current = false;
      setIsThinking(false);
    }
  }, [botColor, personaKey, botElo]);

  useEffect(() => {
    if (gameOver || isThinking || message) {
      return;
    }

    const turnColor = game.turn() === 'w' ? 'white' : 'black';
    if (turnColor === botColor) {
      requestBotMove();
    }
  }, [botColor, game, gameOver, isThinking, message, requestBotMove]);

  function resetGame() {
    setGame(new Chess());
    setBotElo(randomEloInRange(tier.minElo, tier.maxElo));
    setLastMoveSan(null);
    setMessage(null);
    setCanRetry(false);
    setSelectedSquare(null);
    botMoveInFlightRef.current = false;
    setIsThinking(false);
  }

  function tryMove(sourceSquare: string, targetSquare: string): boolean {
    if (!humanCanMove || sourceSquare === targetSquare) {
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
    setLastMoveSan(move.san);
    setMessage(null);
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

    const legalMove = gameRef.current
      .moves({ square: selectedSquare as Square, verbose: true })
      .some((move) => move.to === square);

    if (legalMove) {
      tryMove(selectedSquare, square);
      return;
    }

    setSelectedSquare(isOwnPiece ? square : null);
  }

  const highlightSquares = useMemo<Record<string, React.CSSProperties>>(() => {
    if (!selectedSquare) {
      return {};
    }
    return { [selectedSquare]: { backgroundColor: 'rgba(255, 170, 0, 0.35)' } };
  }, [selectedSquare]);

  const hintSquares = useMemo<Record<string, 'dot' | 'ring'>>(() => {
    if (!selectedSquare || !humanCanMove) {
      return {};
    }
    try {
      const legalMoves = game.moves({ square: selectedSquare as Square, verbose: true });
      const hints: Record<string, 'dot' | 'ring'> = {};
      for (const move of legalMoves) {
        hints[move.to] = game.get(move.to as Square) ? 'ring' : 'dot';
      }
      return hints;
    } catch {
      return {};
    }
  }, [game, humanCanMove, selectedSquare]);

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
    <div className="relative h-[calc(100vh-3rem)] w-full overflow-y-auto overflow-x-hidden px-6 py-6 text-white lg:overflow-hidden lg:px-12 [background-image:url(/walnut-dark.webp)] [background-size:cover] [background-position:center]">
      <div className="mx-auto flex h-full max-w-[1600px] flex-col gap-4">
        <header className="flex flex-wrap items-center gap-3">
          <Link
            href="/train/sparring"
            onClick={(event) => {
              if (
                isGameInProgress &&
                !window.confirm("Leave this game? Your progress will be lost.")
              ) {
                event.preventDefault();
              }
            }}
            className="flex h-9 items-center gap-1.5 rounded-lg border border-black/50 bg-black/40 px-3 text-sm font-semibold text-[#f7e5c6]/70 transition hover:bg-black/60 hover:text-[#f7e5c6]"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="m15 18-6-6 6-6" />
            </svg>
            <span>Personas</span>
          </Link>

          <div className="flex min-w-0 items-center gap-2 rounded-xl border border-black/50 bg-black/40 px-3 py-1.5">
            <Image
              src={persona.icon}
              alt=""
              width={48}
              height={48}
              draggable={false}
              className="h-6 w-6 select-none object-contain"
            />
            <span className="truncate text-sm font-semibold text-[#f7e5c6]">
              {persona.name}
            </span>
          </div>

          <div className="rounded-xl border border-black/50 bg-black/40 px-3 py-1.5 text-sm font-semibold text-[#f7e5c6]/80">
            {tier.name}
          </div>

          <div className="rounded-xl border border-black/50 bg-black/40 px-3 py-1.5 text-sm font-semibold capitalize text-[#f7e5c6]/80">
            You play {color}
          </div>

          <div className="flex-1" />

          <button
            type="button"
            onClick={resetGame}
            className="h-9 rounded-lg border border-[#f7e5c6]/25 bg-black/45 px-4 text-sm font-semibold text-[#f7e5c6]/80 transition hover:bg-black/65"
          >
            Reset
          </button>
        </header>

        <div className="flex min-h-0 flex-1 items-center justify-center">
          <div className="relative aspect-square h-full max-h-full w-auto max-w-full">
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
                    boardOrientation: color,
                    allowDragging: humanCanMove,
                    canDragPiece: ({ piece }) =>
                      humanCanMove && piece.pieceType[0] === gameRef.current.turn(),
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
        </div>

        <footer className="flex shrink-0 items-center justify-between gap-3 rounded-2xl border border-black/50 bg-black/40 px-4 py-2.5">
          <div className="flex min-w-0 items-center gap-2">
            <span className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[#f7e5c6]/55">
              {gameOver ? 'Finished' : isThinking ? 'Thinking' : 'Your move'}
            </span>
            {isThinking && (
              <span className="h-3 w-3 rounded-full border-2 border-[#f7e5c6]/35 border-t-[#f7e5c6] animate-spin" />
            )}
            {lastMoveSan && !gameOver && (
              <span className="truncate text-[11px] text-[#f7e5c6]/65">
                {persona.name} played {lastMoveSan}
              </span>
            )}
            {resultText && (
              <span className="truncate text-[11px] font-semibold text-[#efd9a7]">
                {resultText}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            {message && (
              <span
                role="alert"
                className="max-w-[16rem] truncate rounded-lg border border-red-400/30 bg-red-400/10 px-2 py-1 text-[11px] text-red-300"
                title={message}
              >
                {message}
              </span>
            )}
            {message && canRetry && (
              <button
                type="button"
                onClick={() => setMessage(null)}
                className="h-7 rounded-full border border-[#f7e5c6]/25 bg-black/45 px-3 text-[11px] font-semibold text-[#f7e5c6]/80 transition hover:bg-black/65"
              >
                Retry
              </button>
            )}
          </div>
        </footer>
      </div>
    </div>
  );
}

function describeGameEnd(game: Chess): string | null {
  if (!game.isGameOver()) {
    return null;
  }
  if (game.isCheckmate()) {
    const winner = game.turn() === 'w' ? 'Black' : 'White';
    return `${winner} wins by checkmate`;
  }
  if (game.isStalemate()) {
    return 'Stalemate, draw';
  }
  if (game.isInsufficientMaterial()) {
    return 'Insufficient material, draw';
  }
  if (game.isThreefoldRepetition()) {
    return 'Threefold repetition, draw';
  }
  if (game.isDraw()) {
    return 'Draw';
  }
  return 'Game over';
}

function uciToMove(uci: string) {
  return {
    from: uci.slice(0, 2),
    to: uci.slice(2, 4),
    promotion: uci.slice(4, 5) || undefined,
  };
}
