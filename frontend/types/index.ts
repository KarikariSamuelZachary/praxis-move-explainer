export interface Puzzle {
  id: string;
  fen: string;
  moves: string[];
  rating: number;
  themes: string[];
  gameUrl?: string;
  previousMove?: string;
}

export interface PuzzleAttempt {
  puzzleId: string;
  solved: boolean;
  timeSeconds: number;
  attemptNumber: number; // which woodpecker cycle
  date: string;
}

export interface WoodpeckerSession {
  puzzles: Puzzle[];
  currentIndex: number;
  cycle: number;
  solvedCount: number;
  startTime: number;
}

export interface ExplanationRequest {
  fen: string;
  move: string;
  isCorrect: boolean;
  playerElo: number;
  puzzleThemes: string[];
}

export interface ExplanationResponse {
  explanation: string;
  concept: string;
  tip?: string;
}

export interface GameReviewMove {
  fen: string;
  san: string;
  color: 'white' | 'black';
  classification: 'book' | 'best' | 'excellent' | 'good' | 'inaccuracy' | 'mistake' | 'blunder';
  cp_loss: number;
  explanation?: {
    explanation: string;
    concept?: string;
    tip?: string;
  };
}

export interface UserSettings {
  elo: number;
  puzzleRatingMin: number;
  puzzleRatingMax: number;
  themes: string[];
  dailyPuzzleGoal: number;
}
