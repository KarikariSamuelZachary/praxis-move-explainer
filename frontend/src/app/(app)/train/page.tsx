import { getEndgameRecommendation } from '@/lib/endgame-recommendation';

import TrainPageClient from './TrainPageClient';

/**
 * Awaiting the recommendation here (instead of fetching it in the client)
 * makes the card part of the server-rendered first paint, so the user never
 * sees a skeleton or a placeholder theme that switches to the real one.
 */
export default async function TrainPage() {
  const recommendation = await getEndgameRecommendation();

  return <TrainPageClient recommendation={recommendation} />;
}
