import 'server-only';

import { auth } from '@clerk/nextjs/server';

import { getBackendConfig } from '@/lib/backend';

/**
 * Server-side read of the Train page's "Recommended For You" card data.
 *
 * WHY THIS IS NOT A CLIENT FETCH
 * ==============================
 * The card must show the recommended material category on the first paint:
 * no skeleton, no placeholder category, no swap from loading state to real
 * data. Resolving it here, before the page renders, is the only way to
 * guarantee that -- and it keeps the backend secret and the user id off the
 * client.
 */

export type EndgameRecommendation = {
  category: string;
  sample_fen: string | null;
};

// Matches STARTER_CATEGORY in services/endgame_recommendation.py: the
// king-and-pawn fundamentals category every deployment has drillable
// content for. Used only when the backend cannot answer, so the card is
// never absent.
const STARTER_RECOMMENDATION: EndgameRecommendation = {
  category: 'pure_pawn',
  sample_fen: null,
};

const BACKEND_TIMEOUT_MS = 2000;

/**
 * The backend picks the category this user is weakest at (weighted weakness
 * score over failed drills + their review attempts -- see
 * services/endgame_recommendation.py). Returns null when the backend does
 * not answer or has no category to recommend.
 */
async function fetchEndgameRecommendation(
  userId: string
): Promise<EndgameRecommendation | null> {
  const { backendApiUrl, internalSecret } = getBackendConfig();

  try {
    const response = await fetch(
      new URL('/api/endgames/recommendation', backendApiUrl),
      {
        cache: 'no-store',
        signal: AbortSignal.timeout(BACKEND_TIMEOUT_MS),
        headers: {
          Accept: 'application/json',
          'X-Internal-Secret': internalSecret,
          'X-Clerk-User-Id': userId,
        },
      }
    );
    if (!response.ok) return null;

    const data = (await response.json()) as Partial<EndgameRecommendation>;
    if (!data.category) return null;

    return { category: data.category, sample_fen: data.sample_fen ?? null };
  } catch (error) {
    console.error('Endgame recommendation fetch failed:', error);
    return null;
  }
}

/**
 * The recommendation the Train page renders. Always returns a category --
 * signed-out users and backend failures fall back to the starter category
 * rather than hiding the card.
 */
export async function getEndgameRecommendation(): Promise<EndgameRecommendation> {
  try {
    const { userId } = await auth();
    if (!userId) return STARTER_RECOMMENDATION;
    return (await fetchEndgameRecommendation(userId)) ?? STARTER_RECOMMENDATION;
  } catch (error) {
    console.error('Endgame recommendation lookup failed:', error);
    return STARTER_RECOMMENDATION;
  }
}
