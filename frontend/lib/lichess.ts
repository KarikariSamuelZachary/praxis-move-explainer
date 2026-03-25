import { Puzzle } from '@/types';
import { Chess } from 'chess.js';

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

    // Extract FEN from PGN - the puzzle starts from a specific position
    const fen = extractFenFromPgn(data.game.pgn, data.puzzle.initialPly);

    return {
      id: data.puzzle.id,
      fen,
      moves: data.puzzle.solution,
      rating: data.puzzle.rating,
      themes: data.puzzle.themes,
      gameUrl: `https://lichess.org/training/${data.puzzle.id}`,
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

// Extract the starting FEN for the puzzle from the game PGN
function extractFenFromPgn(pgn: string, initialPly?: number): string {
  const fenMatch = pgn.match(/\[FEN "([^"]+)"\]/);
  const startingFen = fenMatch?.[1];

  if (typeof initialPly !== 'number' || initialPly < 0) {
    return startingFen ?? 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';
  }

  const game = startingFen ? new Chess(startingFen) : new Chess();

  try {
    game.loadPgn(pgn);
    const history = game.history({ verbose: true });

    const replay = startingFen ? new Chess(startingFen) : new Chess();
    for (const move of history.slice(0, initialPly)) {
      replay.move(move);
    }

    return replay.fen();
  } catch (error) {
    console.error('Failed to extract FEN from PGN:', error);
    return startingFen ?? 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';
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
