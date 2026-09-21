import { NextResponse } from 'next/server';
import { auth } from '@clerk/nextjs/server';
import { getBackendConfig } from '@/lib/backend';

// Proxy for the endgame review queue (Woodpecker's Endgames tab). A
// separate queue from GET /api/woodpecker/queue: due cards from
// endgame_woodpecker_entries, each carrying the full drill payload in the
// GET /api/endgames/next shape. The Woodpecker page also derives the tab
// badge from this array's length, exactly like the Puzzles page derives
// "Reviews Due" from the puzzle queue it renders.
export async function GET() {
  const { backendApiUrl, internalSecret } = getBackendConfig();
  const backendUrl = new URL(
    '/api/endgames/woodpecker/queue',
    backendApiUrl
  );

  try {
    const { userId } = await auth();
    if (!userId) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    const response = await fetch(backendUrl, {
      cache: 'no-store',
      headers: {
        Accept: 'application/json',
        'X-Internal-Secret': internalSecret,
        'X-Clerk-User-Id': userId,
      },
    });

    return new NextResponse(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: {
        'content-type': response.headers.get('content-type') ?? 'application/json',
      },
    });
  } catch (error) {
    console.error('Endgame woodpecker queue proxy error:', error);
    return NextResponse.json(
      { error: 'Endgame backend is unreachable' },
      { status: 502 }
    );
  }
}
