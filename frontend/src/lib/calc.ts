import {
  CalcExercise,
  CalcRatingEntry,
  CalcSubmitResult,
  CalcWhyResult,
  CalcWoodpeckerQueueItem,
} from '@/types';

/**
 * Client for the Blind Calculation trainer (Vercel proxy routes -> FastAPI).
 * The exercise is stateless from the client's view: fetch an exercise (with
 * its bearer token), collect 2n-1 plies locally with no grading, submit.
 */

export class CalcFetchError extends Error {
  status: number;
  retryable: boolean;
  constructor(message: string, status: number, retryable: boolean) {
    super(message);
    this.name = 'CalcFetchError';
    this.status = status;
    this.retryable = retryable;
  }
}

/**
 * Depth auto-selection by rating band: the server grades per-depth ratings,
 * so the entry depth (n=3) rating decides how deep the user must calculate.
 * Tunable; mirrors the corpus bands loosely.
 */
export function depthForRating(rating: number | null): number {
  if (rating == null) return 3;
  if (rating < 1400) return 3;
  if (rating < 1800) return 4;
  return 5;
}

async function readError(response: Response, fallback: string): Promise<string> {
  try {
    const data = await response.json();
    if (typeof data?.detail === 'string') return data.detail;
    if (typeof data?.error === 'string') return data.error;
  } catch {
    // fall through to the fallback copy
  }
  return fallback;
}

export async function fetchNextCalcExercise(n: number): Promise<CalcExercise> {
  let response: Response;
  try {
    response = await fetch(`/api/calc/next?n=${n}`, { cache: 'no-store' });
  } catch {
    throw new CalcFetchError('Could not reach the server.', 0, true);
  }
  if (!response.ok) {
    throw new CalcFetchError(
      response.status === 404
        ? 'No calculation exercises are available right now.'
        : await readError(response, 'Could not load an exercise.'),
      response.status,
      response.status >= 500
    );
  }
  return (await response.json()) as CalcExercise;
}

export async function submitCalcLine(
  exerciseToken: string,
  moves: string[],
  hintsUsed = 0
): Promise<CalcSubmitResult> {
  let response: Response;
  try {
    response = await fetch('/api/calc/submit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        exercise_token: exerciseToken,
        moves,
        hints_used: hintsUsed,
      }),
    });
  } catch {
    throw new CalcFetchError('Could not reach the server.', 0, true);
  }
  if (!response.ok) {
    throw new CalcFetchError(
      await readError(response, 'Submission failed.'),
      response.status,
      response.status >= 500
    );
  }
  return (await response.json()) as CalcSubmitResult;
}

export interface CalcHint {
  uci: string;
  ply: number;
}
export async function fetchCalcHint(args: {
  exerciseToken?: string;
  reviewToken?: string;
  pliesEntered: number;
}): Promise<CalcHint> {
  let response: Response;
  try {
    response = await fetch('/api/calc/hint', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        exercise_token: args.exerciseToken ?? null,
        review_token: args.reviewToken ?? null,
        plies_entered: args.pliesEntered,
      }),
    });
  } catch {
    throw new CalcFetchError('Could not reach the server.', 0, true);
  }
  if (!response.ok) {
    throw new CalcFetchError(
      await readError(response, 'Could not get a hint.'),
      response.status,
      response.status >= 500
    );
  }
  return (await response.json()) as CalcHint;
}

export async function fetchRetryToken(
  exerciseToken: string
): Promise<{ exercise_token: string }> {
  let response: Response;
  try {
    response = await fetch('/api/calc/retry', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ exercise_token: exerciseToken }),
    });
  } catch {
    throw new CalcFetchError('Could not reach the server.', 0, true);
  }
  if (!response.ok) {
    throw new CalcFetchError(
      await readError(response, 'Could not start a retry.'),
      response.status,
      response.status >= 500
    );
  }
  return (await response.json()) as { exercise_token: string };
}

export async function fetchCalcReviewQueue(): Promise<CalcWoodpeckerQueueItem[]> {
  let response: Response;
  try {
    response = await fetch('/api/calc/woodpecker/queue', { cache: 'no-store' });
  } catch {
    throw new CalcFetchError('Could not reach the server.', 0, true);
  }
  if (!response.ok) {
    throw new CalcFetchError(
      await readError(response, 'Could not load calculation reviews.'),
      response.status,
      response.status === 429 || response.status >= 500
    );
  }
  return (await response.json()) as CalcWoodpeckerQueueItem[];
}

export async function submitCalcReviewAttempt(
  reviewToken: string,
  moves: string[],
  hintsUsed = 0
): Promise<CalcSubmitResult> {
  let response: Response;
  try {
    response = await fetch('/api/calc/woodpecker/attempt', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        review_token: reviewToken,
        moves,
        hints_used: hintsUsed,
      }),
    });
  } catch {
    throw new CalcFetchError('Could not reach the server.', 0, true);
  }
  if (!response.ok) {
    throw new CalcFetchError(
      await readError(response, 'Review submission failed.'),
      response.status,
      response.status >= 500
    );
  }
  return (await response.json()) as CalcSubmitResult;
}
export async function fetchCalcRatings(): Promise<CalcRatingEntry[]> {
  let response: Response;
  try {
    response = await fetch('/api/calc/ratings', { cache: 'no-store' });
  } catch {
    throw new CalcFetchError('Could not reach the server.', 0, true);
  }
  if (!response.ok) {
    throw new CalcFetchError(
      await readError(response, 'Could not load ratings.'),
      response.status,
      response.status >= 500
    );
  }
  const data = await response.json();
  return (data?.ratings ?? []) as CalcRatingEntry[];
}

export async function fetchWhy(
  attemptId: string,
  kind: 'solve' | 'review' = 'solve'
): Promise<CalcWhyResult> {
  let response: Response;
  try {
    response = await fetch('/api/calc/why', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ attempt_id: attemptId, kind }),
    });
  } catch {
    throw new CalcFetchError('Could not reach the server.', 0, true);
  }
  if (!response.ok) {
    throw new CalcFetchError(
      await readError(response, 'Could not explain this attempt.'),
      response.status,
      response.status === 429 || response.status >= 500
    );
  }
  return (await response.json()) as CalcWhyResult;
}
