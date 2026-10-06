/**
 * Pure derivations extracted from `review/page.tsx`.
 *
 * Shared derivations used by the review route and its tree selectors.
 */
import { GameReviewMove } from '../../../types';

export type ReviewExplanation = NonNullable<GameReviewMove['explanation']>;

export function formatMoveNumber(activePly: number): string {
  if (activePly === 0) {
    return 'Starting position';
  }
  const moveIndex = activePly - 1;
  const fullMove = Math.floor(moveIndex / 2) + 1;
  const suffix = moveIndex % 2 === 0 ? 'White' : 'Black';
  return `Move ${fullMove} · ${suffix}`;
}

export function bestMoveSanFor(currentMove: GameReviewMove | null): string | null {
  return currentMove &&
    currentMove.best_move_san &&
    currentMove.san !== 'Start' &&
    currentMove.classification !== 'book' &&
    currentMove.classification !== 'best'
    ? currentMove.best_move_san
    : null;
}

export function displayedExplanationFor(
  currentMove: GameReviewMove | null,
  coachExplanation: ReviewExplanation | null,
): ReviewExplanation | null {
  return currentMove?.explanation ?? coachExplanation;
}

export function lastPlyFor(moves: GameReviewMove[]): number {
  return Math.max(0, moves.length - 1);
}
