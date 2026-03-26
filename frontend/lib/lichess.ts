import { Puzzle } from '@/types';
import { Chess, Move } from 'chess.js';

// Lichess puzzle themes you can filter by
export const PUZZLE_THEMES = [
  'mate', 'mateIn1', 'mateIn2', 'mateIn3',
  'fork', 'pin', 'skewer', 'discoveredAttack',
  'sacrifice', 'deflection', 'clearance',
  'endgame', 'rookEndgame', 'queenEndgame',
  'opening', 'middlegame',
  'backRankMate', 'smotheredMate',
  'hangingPiece', 'trappedPiece',
  'advancedPawn', 'promotion',
] as const;

export type PuzzleTheme = typeof PUZZLE_THEMES[number];

interface LichessPuzzleResponse {
  puzzle: {
    id: string;
    rating: number;
    themes: string[];
    solution: string[];
    gameId: string;
    initialPly?: number;
  };
  game: {
    pgn: string;
  };
}

type NormalizedPuzzlePosition = {
  fen: string;
  previousMove?: string;
};

const START_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';

function toUci(move: Pick<Move, 'from' | 'to'> & { promotion?: string }): string {
  return `${move.from}${move.to}${move.promotion ?? ''}`;
}

function tryApplyUciMove(game: Chess, uciMove: string): boolean {
  const from = uciMove.slice(0, 2);
  const to = uciMove.slice(2, 4);
  const promotion = uciMove.length > 4 ? uciMove[4] : undefined;

  try {
    game.move({ from, to, promotion });
    return true;
  } catch {
    return false;
  }
}

function buildBoardAtPly(
  history: Move[],
  startingFen: string | undefined,
  plyCount: number
): { game: Chess; previousMove?: string } {
  const replay = startingFen ? new Chess(startingFen) : new Chess();

  for (const move of history.slice(0, plyCount)) {
    replay.move(move);
  }

  const previousMove = plyCount > 0 ? toUci(history[plyCount - 1]) : undefined;

  return {
    game: replay,
    previousMove,
  };
}

function buildCandidatePlies(initialPly: number | undefined, maxPly: number): number[] {
  if (typeof initialPly !== 'number') {
    return Array.from({ length: maxPly + 1 }, (_, index) => index);
  }

  const candidates = new Set<number>();
  for (const delta of [0, -1, 1, -2, 2]) {
    const ply = initialPly + delta;
    if (ply >= 0 && ply <= maxPly) {
      candidates.add(ply);
    }
  }

  return [...candidates];
}

function normalizePuzzlePosition(
  pgn: string,
  initialPly: number | undefined,
  solution: string[]
): NormalizedPuzzlePosition {
  const fenMatch = pgn.match(/\[FEN "([^"]+)"\]/);
  const startingFen = fenMatch?.[1];
  const pgnGame = startingFen ? new Chess(startingFen) : new Chess();

  try {
    pgnGame.loadPgn(pgn);
    const history = pgnGame.history({ verbose: true });
    const firstSolutionMove = solution[0];

    for (const plyCount of buildCandidatePlies(initialPly, history.length)) {
      const { game, previousMove } = buildBoardAtPly(history, startingFen, plyCount);

      // We normalize to the position the user should solve right now, so
      // the first solution move must be legal from the returned board.
      if (!firstSolutionMove) {
        return { fen: game.fen(), previousMove };
      }

      const probe = new Chess(game.fen());
      if (tryApplyUciMove(probe, firstSolutionMove)) {
        return {
          fen: game.fen(),
          previousMove,
        };
      }
    }
  } catch (error) {
    console.error('Failed to normalize Lichess puzzle position:', error);
  }

  return {
    fen: startingFen ?? START_FEN,
  };
}

// Fetch a single puzzle from Lichess API by theme and rating
export async function fetchLichessPuzzle(
  theme?: string,
  _minRating?: number,
  _maxRating?: number
): Promise<Puzzle> {
  try {
    void _minRating;
    void _maxRating;

    // Build query params
    const params = new URLSearchParams();
    if (theme) params.append('themes', theme);

    const response = await fetch(
      `https://lichess.org/api/puzzle/next?${params.toString()}`,
      {
        headers: {
          Accept: 'application/json',
        },
      }
    );

    if (!response.ok) {
      throw new Error(`Lichess API error: ${response.status}`);
    }

    const data: LichessPuzzleResponse = await response.json();

    const normalizedPosition = normalizePuzzlePosition(
      data.game.pgn,
      data.puzzle.initialPly,
      data.puzzle.solution
    );

    return {
      id: data.puzzle.id,
      fen: normalizedPosition.fen,
      moves: data.puzzle.solution,
      rating: data.puzzle.rating,
      themes: data.puzzle.themes,
      gameUrl: `https://lichess.org/training/${data.puzzle.id}`,
      previousMove: normalizedPosition.previousMove,
    };
  } catch (error) {
    console.error('Failed to fetch Lichess puzzle:', error);
    // Return a fallback puzzle if API fails
    return getFallbackPuzzle();
  }
}

// Fetch multiple puzzles for a Woodpecker session
export async function fetchPuzzleBatch(
  count: number = 10,
  theme?: string,
  minRating: number = 1000,
  maxRating: number = 2000
): Promise<Puzzle[]> {
  const puzzlePromises = Array.from({ length: count }, () =>
    fetchLichessPuzzle(theme, minRating, maxRating)
  );

  const puzzles = await Promise.allSettled(puzzlePromises);

  return puzzles
    .filter((p): p is PromiseFulfilledResult<Puzzle> => p.status === 'fulfilled')
    .map((p) => p.value);
}

// Fallback puzzles when API is unavailable - classic tactical positions
function getFallbackPuzzle(): Puzzle {
  const fallbacks: Puzzle[] = [
    {
      id: 'fallback_1',
      fen: 'r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4',
      moves: ['f3g5', 'f6e4', 'g5f7'],
      rating: 1200,
      themes: ['fork', 'sacrifice'],
    },
    {
      id: 'fallback_2',
      fen: '4k3/8/4K3/4R3/8/8/8/8 w - - 0 1',
      moves: ['e5e8'],
      rating: 800,
      themes: ['mateIn1', 'rookEndgame'],
    },
    {
      id: 'fallback_3',
      fen: 'r1b1k2r/pppp1ppp/2n2n2/2b1p1N1/2B1P3/8/PPPP1PPP/RNBQK2R w KQkq - 0 1',
      moves: ['g5f7', 'e8f7', 'c4d5'],
      rating: 1500,
      themes: ['fork', 'sacrifice'],
    },
  ];

  return fallbacks[Math.floor(Math.random() * fallbacks.length)];
}

// Get puzzle difficulty label
export function getPuzzleDifficultyLabel(rating: number): string {
  if (rating < 1000) return 'Beginner';
  if (rating < 1400) return 'Intermediate';
  if (rating < 1800) return 'Advanced';
  if (rating < 2200) return 'Expert';
  return 'Master';
}

// Get color from FEN string
export function getColorToPlayFromFen(fen: string): 'white' | 'black' {
  const parts = fen.split(' ');
  return parts[1] === 'w' ? 'white' : 'black';
}
