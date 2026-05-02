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

interface PuzzleResponse {
  id: string;
  fen: string;
  moves: string[];
  rating: number;
  themes: string[];
  gameUrl?: string;
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
  minRating?: number,
  maxRating?: number
): Promise<Puzzle> {
  try {
    const params = new URLSearchParams();
    if (theme) params.append('theme', theme);
    if (minRating) params.append('min_rating', minRating.toString());
    if (maxRating) params.append('max_rating', maxRating.toString());
    params.append('limit', '1');

    const response = await fetch(
      `http://localhost:8000/api/puzzles?${params.toString()}`,
      {
        headers: {
          Accept: 'application/json',
        },
      }
    );

    if (!response.ok) {
      throw new Error(`Praxis API error: ${response.status}`);
    }

    const data = await response.json();
    if (!Array.isArray(data) || data.length === 0) {
      throw new Error('No puzzles returned from Praxis API');
    }
    const puzzle = data[0];

    return {
      id: puzzle.id,
      fen: puzzle.fen,
      moves: puzzle.moves,
      rating: puzzle.rating,
      themes: puzzle.themes,
      gameUrl: puzzle.gameUrl,
    };
  } catch (error) {
    console.error('Failed to fetch puzzle from Praxis API:', error);
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
  try {
    const params = new URLSearchParams();
    if (theme) params.append('theme', theme);
    if (minRating) params.append('min_rating', minRating.toString());
    if (maxRating) params.append('max_rating', maxRating.toString());
    params.append('limit', count.toString());

    const response = await fetch(
      `http://localhost:8000/api/puzzles?${params.toString()}`,
      { headers: { Accept: 'application/json' } }
    );

    if (!response.ok) {
      throw new Error(`Praxis API error: ${response.status}`);
    }

    const data = await response.json();
    if (!Array.isArray(data) || data.length === 0) {
      throw new Error('No puzzles returned from Praxis API');
    }

    return data.map((puzzle: PuzzleResponse) => {
      let normalizedFen = puzzle.fen;
      let solutionMoves = puzzle.moves;
      let previousMove: string | undefined;
      const setupMove = puzzle.moves[0];

      if (setupMove) {
        try {
          const chess = new Chess(puzzle.fen);
          chess.move({
            from: setupMove.slice(0, 2),
            to: setupMove.slice(2, 4),
            promotion: setupMove[4] || undefined,
          });
          normalizedFen = chess.fen();
          solutionMoves = puzzle.moves.slice(1);
          previousMove = setupMove;
        } catch {
          normalizedFen = puzzle.fen;
          solutionMoves = puzzle.moves;
        }
      }

      return {
        id: puzzle.id,
        fen: normalizedFen,
        moves: solutionMoves,
        rating: puzzle.rating,
        themes: puzzle.themes,
        gameUrl: puzzle.gameUrl,
        previousMove,
      };
    });
  } catch (error) {
    console.error('Failed to fetch puzzle batch from Praxis API:', error);
    return Array.from({ length: count }, () => getFallbackPuzzle());
  }
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
