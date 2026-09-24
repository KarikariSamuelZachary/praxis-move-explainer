'use client';

import dynamic from 'next/dynamic';
import Image from 'next/image';
import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useUser } from '@clerk/nextjs';
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

// Stable identities so the board's options object does not change on every
// render (react-chessboard re-renders all 64 squares whenever it does).
const EMPTY_HINTS: Record<string, 'dot' | 'ring'> = {};
const EMPTY_STYLES: Record<string, React.CSSProperties> = {};

// The Puzzles / Endgame Trainer hint language, verbatim: only the piece to
// move is highlighted, in emerald, and it fades on its own. The destination
// is deliberately withheld.
const HIGHLIGHT_HINT = 'rgba(16, 185, 129, 0.4)';
const HINT_FADE_MS = 4000;

const DARK_SQUARE_STYLE: React.CSSProperties = {
  backgroundImage: 'url(/walnut-dark.webp)',
  backgroundSize: '110% 110%',
  backgroundPosition: 'center',
};

const LIGHT_SQUARE_STYLE: React.CSSProperties = {
  backgroundImage: 'url(/walnut-light.webp)',
  backgroundSize: '110% 110%',
  backgroundPosition: 'center',
};

const BOARD_STYLE: React.CSSProperties = {
  width: '100%',
  height: '100%',
  borderRadius: '8px',
  boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
};

const woodBoxStyle: React.CSSProperties = {
  borderRadius: '4px',
  background:
    'linear-gradient(rgba(0,0,0,0.5),rgba(0,0,0,0.5)), url(/walnut-dark.webp)',
  backgroundSize: 'cover',
  backgroundPosition: 'center',
  boxShadow:
    '0 0 0 2px #1a0a02, inset 0 2px 0 rgba(255,200,100,0.12), inset 0 -2px 0 rgba(0,0,0,0.5), 0 4px 12px rgba(0,0,0,0.5)',
};

// Same seat-badge styling for both players' corner icons.
const seatBadgeClass =
  'absolute z-20 flex h-11 w-11 items-center justify-center rounded-lg border border-[#e4c197]/40 bg-[#1b120d]/95 shadow-[0_6px_18px_rgba(0,0,0,0.55)]';

// The exact walnut card language of the Puzzles / Endgame Trainer pages.
const CARD_CLASS =
  'rounded-2xl border border-black/50 backdrop-blur-sm [background-image:linear-gradient(rgba(0,0,0,0.5),rgba(0,0,0,0.5)),url(/walnut-dark.webp)] [background-size:cover] [background-position:center] [box-shadow:0_10px_30px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(255,255,255,0.06),inset_0_-1px_0_rgba(0,0,0,0.5)]';

// Endgame Trainer's gold primary assist (Hint), compacted for a 3-up row.
const GOLD_BUTTON_CLASS =
  'flex w-full items-center justify-center gap-1.5 rounded-xl bg-gradient-to-b from-[#eacb90] to-[#c1954f] px-2 py-3 text-xs font-bold text-[#2a1a06] shadow-[0_10px_22px_rgba(0,0,0,0.45),inset_0_1px_0_rgba(255,255,255,0.55)] transition hover:from-[#f2d8a4] hover:to-[#cda261] disabled:cursor-not-allowed disabled:opacity-50';

// Endgame Trainer's secondary action, compacted for a 3-up row.
const SECONDARY_BUTTON_CLASS =
  'flex w-full items-center justify-center gap-1.5 rounded-xl border border-white/15 bg-white/5 px-2 py-3 text-xs font-bold text-white/80 transition hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-50';

// Resign reads as a danger action, not a neutral one.
const DANGER_BUTTON_CLASS =
  'flex w-full items-center justify-center gap-1.5 rounded-xl border border-red-400/25 bg-red-400/10 px-2 py-3 text-xs font-bold text-red-200 transition hover:bg-red-400/20 disabled:cursor-not-allowed disabled:opacity-50';

// The result modal's actions use the Endgame Trainer's FULL-size button
// language; the in-game row above uses the compacted variants.
const MODAL_PRIMARY_BUTTON_CLASS =
  'flex w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-b from-[#eacb90] to-[#c1954f] px-4 py-3.5 text-sm font-bold text-[#2a1a06] shadow-[0_10px_22px_rgba(0,0,0,0.45),inset_0_1px_0_rgba(255,255,255,0.55)] transition hover:from-[#f2d8a4] hover:to-[#cda261]';

const MODAL_SECONDARY_BUTTON_CLASS =
  'flex w-full items-center justify-center gap-2 rounded-xl border border-white/15 bg-white/5 px-4 py-3.5 text-sm font-bold text-white/80 transition hover:bg-white/10';

type HistoryMove = {
  san: string;
  color: 'w' | 'b';
  fen: string;
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

  const router = useRouter();

  // Same seat-initial rule as TopNav's ProfileMenu: sign-up collects no
  // name, so the first letter of the email is the player's mark.
  const { user } = useUser();
  const email =
    user?.primaryEmailAddress?.emailAddress ??
    user?.emailAddresses?.[0]?.emailAddress ??
    '';
  const playerInitial = email.trim() ? email.trim()[0].toUpperCase() : '?';

  // The bot's Elo is drawn per game; a rematch re-rolls it.
  const [botElo, setBotElo] = useState(() =>
    randomEloInRange(tier.minElo, tier.maxElo)
  );
  const [game, setGame] = useState(() => new Chess());
  const [viewIndex, setViewIndex] = useState(0);
  const [resigned, setResigned] = useState(false);
  const [isThinking, setIsThinking] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [canRetry, setCanRetry] = useState(false);
  const [selectedSquare, setSelectedSquare] = useState<string | null>(null);
  const [hintsUsed, setHintsUsed] = useState(0);
  // The end-of-game modal: shown when the game resolves (checkmate, draw or
  // resignation), dismissable so the final position and move list stay
  // reviewable.
  const [showResult, setShowResult] = useState(false);
  // The first Hint press highlights only the piece to move (see
  // HIGHLIGHT_HINT), like the Puzzles and Endgame Trainer boards. The fetched
  // move is kept so a SECOND press plays it, standing in for the Endgame
  // Trainer's separate "Show move" assist.
  const [hint, setHint] = useState<{
    from: string;
    to: string;
    promotion?: string;
  } | null>(null);
  // The emerald highlight fades after HINT_FADE_MS; the fetched move itself
  // stays available for the position until a move or a view change.
  const [hintVisible, setHintVisible] = useState(false);
  const [isHintLoading, setIsHintLoading] = useState(false);

  const gameRef = useRef(game);
  const botMoveInFlightRef = useRef(false);
  const moveListRef = useRef<HTMLDivElement>(null);
  const hintTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Latest viewed FEN, for discarding hint answers that land after the board
  // has moved on (same guard as the Endgame Trainer board).
  const viewFenRef = useRef(START_FEN);
  // Bumped on every rematch: answers from the previous game (bot replies,
  // hints) are discarded instead of landing on the new board.
  const gameIdRef = useRef(0);
  // A resignation ends the game immediately, so a bot reply already in
  // flight must not land on the final position.
  const resignedRef = useRef(false);

  useEffect(() => {
    gameRef.current = game;
  }, [game]);

  useEffect(() => {
    resignedRef.current = resigned;
  }, [resigned]);

  const botColor: PlayColor = color === 'white' ? 'black' : 'white';
  const gameOver = game.isGameOver();
  const over = gameOver || resigned;

  // Verbose history is the single source for the move list, the replayed
  // positions and whose turn it is at any of them (chess.js Move carries
  // the SAN, the mover's color and the FEN AFTER the move).
  const history = useMemo<HistoryMove[]>(
    () =>
      game.history({ verbose: true }).map((entry) => ({
        san: entry.san,
        color: entry.color,
        fen: entry.after,
      })),
    [game]
  );

  const viewFen =
    viewIndex === 0 ? START_FEN : (history[viewIndex - 1]?.fen ?? START_FEN);
  useEffect(() => {
    viewFenRef.current = viewFen;
  }, [viewFen]);
  // A read-only Chess for the viewed position: click-selection legality and
  // the destination hints all answer for the position ON the board, which is
  // the reviewed one while browsing and the live one otherwise.
  const viewGame = useMemo(() => new Chess(viewFen), [viewFen]);
  const viewTurn: 'w' | 'b' =
    viewIndex === 0 ? 'w' : history[viewIndex - 1].color === 'w' ? 'b' : 'w';
  const isViewingLive = viewIndex === history.length;
  const humanCanMove =
    !isThinking && !over && (viewTurn === 'w' ? 'white' : 'black') === color;

  const resultText = useMemo(() => {
    if (resigned) {
      return `You resigned — ${persona.name} wins.`;
    }
    return describeGameEnd(game);
  }, [game, resigned, persona.name]);

  const outcome = useMemo(
    () => describeOutcome(game, resigned, color, persona.name),
    [game, resigned, color, persona.name]
  );

  // The result modal pops the moment the game resolves, once per game; the
  // user can dismiss it to review the final position and move list.
  useEffect(() => {
    if (over) {
      setShowResult(true);
    }
  }, [over]);

  // A refresh/reload mid-game loses the board position AND re-picks the
  // bot's Elo (the game is client-owned and deliberately unpersisted), so
  // warn before unload while a game is actually in progress.
  const isGameInProgress = !over && history.length > 0;

  // The beforeunload guard can't see client-side navigation, so the back
  // arrow asks before abandoning a game in progress.
  const handleBack = useCallback(() => {
    if (
      isGameInProgress &&
      !window.confirm('Leave this game? Your progress will be lost.')
    ) {
      return;
    }
    router.push('/train/sparring');
  }, [isGameInProgress, router]);

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

  // Reviewing an older position or a failed hint must not leak into the
  // next position: selection and hint highlights reset with the view.
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

  const requestBotMove = useCallback(async () => {
    if (botMoveInFlightRef.current || gameRef.current.isGameOver()) {
      return;
    }

    botMoveInFlightRef.current = true;
    setIsThinking(true);
    setMessage(null);
    setCanRetry(false);

    const pliesBefore = gameRef.current.history().length;
    // Snapshot the queried line: chess.js drops move history when built from
    // a FEN, and the replayed positions / move list depend on the full line.
    const sansBefore = gameRef.current.history();
    const gameId = gameIdRef.current;

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
      // A rematch replaced the game, or the player resigned, while this
      // reply was in flight: drop it rather than mutating a finished board.
      if (gameIdRef.current !== gameId || resignedRef.current) {
        return;
      }
      const nextGame = replaySans(sansBefore);
      nextGame.move(uciToMove(data.move_uci));
      setGame(nextGame);
      // Follow the reply only when the player is watching the live position;
      // reviewing an earlier move keeps their place.
      setViewIndex((prev) => (prev === pliesBefore ? pliesBefore + 1 : prev));
    } catch (error) {
      setMessage(
        error instanceof Error ? error.message : 'Failed to get a sparring move.'
      );
      setCanRetry((error as { retryable?: boolean })?.retryable !== false);
    } finally {
      // Only the current game owns the thinking flag: a stale reply from the
      // previous game must not unlock the new game's board mid-request.
      if (gameIdRef.current === gameId) {
        botMoveInFlightRef.current = false;
        setIsThinking(false);
      }
    }
  }, [botColor, personaKey, botElo]);

  useEffect(() => {
    if (over || isThinking || message) {
      return;
    }

    const turnColor = game.turn() === 'w' ? 'white' : 'black';
    if (turnColor === botColor) {
      requestBotMove();
    }
  }, [botColor, game, over, isThinking, message, requestBotMove]);

  function goToIndex(ply: number) {
    setViewIndex(ply);
    setSelectedSquare(null);
    setHint(null);
    setHintVisible(false);
    setMessage(null);
  }

  function resignGame() {
    if (over) {
      return;
    }
    setResigned(true);
    setSelectedSquare(null);
    setHint(null);
    setHintVisible(false);
  }

  // Same persona and color, fresh board, and a newly drawn bot Elo (the
  // result modal's "Rematch").
  function rematch() {
    gameIdRef.current += 1;
    botMoveInFlightRef.current = false;
    if (hintTimerRef.current) {
      clearTimeout(hintTimerRef.current);
      hintTimerRef.current = null;
    }
    setGame(new Chess());
    setViewIndex(0);
    setResigned(false);
    setIsThinking(false);
    setMessage(null);
    setCanRetry(false);
    setSelectedSquare(null);
    setHint(null);
    setHintVisible(false);
    setHintsUsed(0);
    setBotElo(randomEloInRange(tier.minElo, tier.maxElo));
    setShowResult(false);
  }

  function undoMove() {
    if (isThinking || over || history.length === 0) {
      return;
    }

    // Take back to the player's previous decision point: the bot's reply
    // goes with the player's move, a lone un-answered player move goes alone.
    const lastPlyWasPlayer =
      (history.length - 1) % 2 === (color === 'white' ? 0 : 1);
    const target = Math.max(0, history.length - (lastPlyWasPlayer ? 1 : 2));
    if (target === history.length) {
      return;
    }

    const rebuilt = buildGameAt(history, target);
    setGame(rebuilt);
    setViewIndex(target);
    setSelectedSquare(null);
    setHint(null);
    setHintVisible(false);
    setMessage(null);
    setCanRetry(false);
    botMoveInFlightRef.current = false;
  }

  async function requestHint() {
    if (!humanCanMove || isHintLoading) {
      return;
    }

    // Second press on a position whose hint was already fetched: play it.
    // The first press only points at the piece, matching the Endgame
    // Trainer's "Hint"; this second press is its "Show move" assist.
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
    setMessage(null);

    // Same endpoint the bot moves use, aimed at the player's side:
    // best_move_* is the canonical engine line for the side to move, so
    // falling back to move_uci covers the "persona already picked best"
    // case where best_move_* is null.
    const fenBefore = viewFen;
    const gameId = gameIdRef.current;

    try {
      const response = await fetch('/api/train/engine-sparring-move', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          fen: fenBefore,
          bot_color: color,
          persona: 'positional',
          target_elo: botElo,
        }),
      });

      if (!response.ok) {
        const body = (await response.json().catch(() => ({}))) as ApiErrorResponse;
        const detail = body.detail ?? body.error;
        throw new Error(
          response.status === 429
            ? 'Rate limited. Wait a few seconds, then retry.'
            : detail ?? `Hint request failed (${response.status})`
        );
      }

      const data = (await response.json()) as EngineSparringMoveResponse;
      const uci = data.best_move_uci ?? data.move_uci;
      if (!uci || uci.length < 4) {
        throw new Error('No hint available for this position.');
      }

      // A slow answer can land after the board has moved on (or a rematch
      // replaced it): a hint for a position that is no longer on the board
      // is never shown (the Endgame Trainer board applies the same
      // stale-response guard).
      if (gameIdRef.current !== gameId || viewFenRef.current !== fenBefore) {
        return;
      }

      // Puzzles-style: only the piece to move is highlighted, and it fades
      // on its own. The destination is deliberately withheld; the fetched
      // move is retained so the next press can play it.
      if (hintTimerRef.current) {
        clearTimeout(hintTimerRef.current);
      }
      setHint(uciToMove(uci));
      setHintVisible(true);
      setHintsUsed((count) => count + 1);
      hintTimerRef.current = setTimeout(() => {
        hintTimerRef.current = null;
        setHintVisible(false);
      }, HINT_FADE_MS);
    } catch (error) {
      setMessage(
        error instanceof Error ? error.message : 'Failed to get a hint.'
      );
    } finally {
      setIsHintLoading(false);
    }
  }

  const tryMove = useCallback(
    (
      sourceSquare: string,
      targetSquare: string,
      promotion = 'q'
    ): boolean => {
      if (!humanCanMove || sourceSquare === targetSquare) {
        return false;
      }

      // Moving from a reviewed position branches: the future after that move
      // is discarded and the game continues from the new choice.
      const baseSans = history.slice(0, viewIndex).map((entry) => entry.san);
      const rebuilt = buildGameAt(history, viewIndex);
      let move: ReturnType<typeof rebuilt.move> | null = null;
      try {
        move = rebuilt.move({
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

      setGame(rebuilt);
      setViewIndex(baseSans.length + 1);
      setMessage(null);
      setCanRetry(false);
      setSelectedSquare(null);
      setHint(null);
      setHintVisible(false);
      return true;
    },
    [history, humanCanMove, viewIndex]
  );

  const handleDrop = useCallback(
    ({
      sourceSquare,
      targetSquare,
    }: {
      sourceSquare: string;
      targetSquare: string | null;
    }) => {
      if (!targetSquare) {
        return false;
      }
      return tryMove(sourceSquare, targetSquare);
    },
    [tryMove]
  );

  const handleSquareClick = useCallback(
    ({ square }: { piece: { pieceType: string } | null; square: string }) => {
      if (!humanCanMove) {
        setSelectedSquare(null);
        return;
      }

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
    },
    [humanCanMove, selectedSquare, tryMove, viewGame, viewTurn]
  );

  const squareStyles = useMemo<Record<string, React.CSSProperties>>(() => {
    const hintFrom = hintVisible ? hint?.from : null;
    if (!selectedSquare && !hintFrom) {
      return EMPTY_STYLES;
    }
    const styles: Record<string, React.CSSProperties> = {};
    // The piece-only hint sits under the selection tint, matching the
    // Endgame Trainer board: the user's own selection always wins the square.
    if (hintFrom) {
      styles[hintFrom] = { backgroundColor: HIGHLIGHT_HINT };
    }
    if (selectedSquare) {
      styles[selectedSquare] = { backgroundColor: 'rgba(255, 170, 0, 0.35)' };
    }
    return styles;
  }, [hint, hintVisible, selectedSquare]);

  const hintSquares = useMemo<Record<string, 'dot' | 'ring'>>(() => {
    if (!selectedSquare || !humanCanMove) {
      return EMPTY_HINTS;
    }
    try {
      const legalMoves = viewGame.moves({
        square: selectedSquare as Square,
        verbose: true,
      });
      const hints: Record<string, 'dot' | 'ring'> = {};
      for (const move of legalMoves) {
        hints[move.to] = viewGame.get(move.to as Square) ? 'ring' : 'dot';
      }
      return hints;
    } catch {
      return EMPTY_HINTS;
    }
  }, [selectedSquare, humanCanMove, viewGame]);

  const squareRenderer = useCallback<SquareRenderer>(
    ({ square, children }) => {
      const hintMark = hintSquares[square];
      const squareStyle = squareStyles[square];
      return (
        <div className="relative h-full w-full" style={squareStyle}>
          {hintMark === 'dot' && (
            <div className="pointer-events-none absolute left-1/2 top-1/2 h-[30%] w-[30%] -translate-x-1/2 -translate-y-1/2 rounded-full bg-black/25" />
          )}
          {hintMark === 'ring' && (
            <div className="pointer-events-none absolute left-1/2 top-1/2 h-[88%] w-[88%] -translate-x-1/2 -translate-y-1/2 rounded-full border-[6px] border-black/25" />
          )}
          {children}
        </div>
      );
    },
    [hintSquares, squareStyles]
  );

  const canDragPiece = useCallback(
    ({ piece }: { piece: { pieceType: string } }) =>
      humanCanMove && piece.pieceType[0] === (viewTurn === 'w' ? 'w' : 'b'),
    [humanCanMove, viewTurn]
  );

  // One options object per relevant change: react-chessboard re-renders all
  // 64 squares whenever this identity changes, so keep unrelated state
  // (messages, hint spinner, …) out of it.
  const boardOptions = useMemo(
    () => ({
      position: viewFen,
      boardOrientation: color,
      allowDragging: humanCanMove,
      canDragPiece,
      onPieceDrop: handleDrop,
      onSquareClick: handleSquareClick,
      squareRenderer,
      boardStyle: BOARD_STYLE,
      darkSquareStyle: DARK_SQUARE_STYLE,
      lightSquareStyle: LIGHT_SQUARE_STYLE,
      darkSquareNotationStyle: { color: '#f0e0c0' },
      lightSquareNotationStyle: { color: '#3a2410' },
      animationDurationInMs: 200,
    }),
    [
      canDragPiece,
      color,
      handleDrop,
      handleSquareClick,
      humanCanMove,
      squareRenderer,
      viewFen,
    ]
  );

  // Pairs of plies: White always opens, so index 0 is White's move.
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

  return (
    <div className="relative -mt-2 h-[calc(100vh-2.5rem)] w-full overflow-y-auto overflow-x-hidden px-6 pb-[10px] pt-16 text-white lg:overflow-hidden lg:px-10 lg:pt-6 [background-image:url(/walnut-dark.webp)] [background-size:cover] [background-position:center]">
      {/* Back button - top-left, same placement as the repertoire subpages. */}
      <button
        type="button"
        onClick={handleBack}
        aria-label="Back to sparring personas"
        className="absolute left-4 top-4 z-30 flex h-9 w-9 items-center justify-center rounded-lg border border-black/50 bg-black/40 text-[#efd9a7] transition hover:bg-black/60 sm:left-6 sm:top-6"
      >
        <BackArrowIcon />
      </button>

      {/* Board + side panel travel as one centered group; the max width is
          exactly board + gap + card so the card hugs the board and the pair
          stays centered on wide screens. */}
      <div className="grid h-full w-full grid-cols-1 gap-6 lg:mx-auto lg:max-w-[calc(100vh-70px+1.5rem+20rem)] lg:grid-cols-[minmax(0,1fr)_20rem]">
        {/* ============ LEFT: CHESSBOARD ============ */}
        <section className="order-1 flex min-h-0 items-center justify-center">
          {/* Same board sizing contract as the review and puzzles pages. */}
          <div className="relative aspect-square w-full max-w-[calc(100vh-70px)]">
            {/* Defender's seat badge, attached to the board's top-left corner. */}
            <div className={`${seatBadgeClass} -left-3 -top-3`}>
              <Image
                src={persona.icon}
                alt={persona.name}
                width={48}
                height={48}
                draggable={false}
                className="h-8 w-8 select-none object-contain"
              />
            </div>

            {/* Player's seat badge (email initial), just off the board's bottom-right corner. */}
            <div className={`${seatBadgeClass} -right-3 bottom-[-6px]`}>
              <span className="flex h-8 w-8 items-center justify-center rounded-full border border-[#e4c197]/45 bg-[#c49a7a] text-sm font-bold text-black shadow-[inset_0_1px_2px_rgba(255,255,255,0.45)]">
                {playerInitial}
              </span>
            </div>

            {(over || message) && (
              <div
                className={`absolute left-1/2 top-3 z-30 flex max-w-[92%] -translate-x-1/2 items-center gap-2 rounded-full border px-3 py-1.5 text-[11px] shadow-lg backdrop-blur-sm ${
                  over
                    ? 'border-[#d9b87c]/40 bg-black/80 font-semibold text-[#f7e5c6]'
                    : 'border-red-400/30 bg-black/80 text-red-300'
                }`}
              >
                {over ? (
                  <span role="status">{resultText}</span>
                ) : (
                  <>
                    <span role="alert" className="truncate" title={message ?? undefined}>
                      {message}
                    </span>
                    {canRetry && (
                      <button
                        type="button"
                        onClick={() => setMessage(null)}
                        className="shrink-0 rounded-full border border-[#f7e5c6]/25 bg-black/45 px-2.5 py-0.5 text-[11px] font-semibold text-[#f7e5c6]/80 transition hover:bg-black/65"
                      >
                        Retry
                      </button>
                    )}
                  </>
                )}
              </div>
            )}

            <div className="relative h-full w-full">
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
                  <Chessboard options={boardOptions} />
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* ============ RIGHT: MOVE LIST + ACTIONS ============ */}
        <section className="order-2 flex min-h-0 flex-col gap-5">
          {/* Move-history box: every ply played, click to review that position. */}
          <div className={`${CARD_CLASS} flex min-h-0 flex-1 flex-col p-5`}>
            <div className="flex shrink-0 items-center justify-between gap-2">
              <span className="text-[11px] font-bold uppercase tracking-[0.3em] text-[#f7e5c6]/60">
                Moves
              </span>
            </div>

            <div
              ref={moveListRef}
              className="wood-scrollbar mt-3 min-h-0 flex-1 overflow-y-auto rounded-lg border border-white/10 bg-black/30 p-2"
            >
              {history.length === 0 ? (
                <p className="px-2 py-3 text-[12px] leading-5 text-white/40">
                  {color === 'white'
                    ? 'No moves yet — play your first move on the board.'
                    : 'The engine opens while you watch — moves appear here.'}
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
                        className={moveCellClass(viewIndex === row.startPly + 1)}
                      >
                        {row.white}
                      </button>
                      {row.black ? (
                        <button
                          type="button"
                          onClick={() => goToIndex(row.startPly + 2)}
                          className={moveCellClass(viewIndex === row.startPly + 2)}
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

          {/* Resign / Hint / Undo, one line, Endgame Trainer button language. */}
          <div className={`${CARD_CLASS} shrink-0 p-5`}>
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
        </section>
      </div>

      {showResult && over && (
        <GameOverModal
          outcome={outcome}
          moves={Math.ceil(history.length / 2)}
          hintsUsed={hintsUsed}
          botElo={botElo}
          onRematch={rematch}
          onNewBot={() => router.push('/train/sparring')}
          onClose={() => setShowResult(false)}
        />
      )}
    </div>
  );
}

type GameOutcome = { title: string; subtitle: string };

// The result modal's headline + reason, mirroring the reference's
// "Nora Won / by resignation" shape with this app's names.
function describeOutcome(
  game: Chess,
  resigned: boolean,
  playerColor: PlayColor,
  personaName: string
): GameOutcome {
  if (resigned) {
    return { title: `${personaName} Won`, subtitle: 'by resignation' };
  }
  if (game.isCheckmate()) {
    const winner: PlayColor = game.turn() === 'w' ? 'black' : 'white';
    return {
      title: winner === playerColor ? 'You Won' : `${personaName} Won`,
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

function describeGameEnd(game: Chess): string | null {
  if (!game.isGameOver()) {
    return null;
  }
  if (game.isCheckmate()) {
    const winner = game.turn() === 'w' ? 'Black' : 'White';
    return `${winner} wins by checkmate`;
  }
  if (game.isStalemate()) {
    return 'Stalemate — draw';
  }
  if (game.isInsufficientMaterial()) {
    return 'Insufficient material — draw';
  }
  if (game.isThreefoldRepetition()) {
    return 'Threefold repetition — draw';
  }
  if (game.isDraw()) {
    return 'Draw';
  }
  return 'Game over';
}

// The end-of-game card: who won and why, a small game summary, and the two
// follow-ups (fresh persona pick or a rematch against a newly drawn bot).
// Same modal shell as the persona start dialog on the setup page; no
// account CTA because the player is already signed in.
function GameOverModal({
  outcome,
  moves,
  hintsUsed,
  botElo,
  onRematch,
  onNewBot,
  onClose,
}: {
  outcome: GameOutcome;
  moves: number;
  hintsUsed: number;
  botElo: number;
  onRematch: () => void;
  onNewBot: () => void;
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
        className={`${CARD_CLASS} relative w-full max-w-md rounded-2xl p-6`}
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
          <ResultStat icon={<RatingIcon />} value={botElo} label="Bot Elo" />
        </div>

        <div className="mt-6 grid grid-cols-2 gap-3">
          <button
            type="button"
            onClick={onNewBot}
            className={MODAL_SECONDARY_BUTTON_CLASS}
          >
            New Bot
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
  value: number;
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

// Replay helpers -----------------------------------------------------------

// Rebuild the position after `ply` plies by replaying the SAN line. Games
// here are short, so replaying from the start is cheaper than caching FEN
// chains and keeps every branch point exact.
function buildGameAt(history: HistoryMove[], ply: number): Chess {
  return replaySans(history.slice(0, ply).map((entry) => entry.san));
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

// A move cell in the history box; the reviewed ply carries the amber tint.
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

function BackArrowIcon() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="m15 18-6-6 6-6" />
    </svg>
  );
}

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

// The "Show move" mark, matching the Endgame Trainer / Puzzles page glyph.
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
