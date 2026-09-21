import { NextRequest, NextResponse } from 'next/server';
import { auth } from '@clerk/nextjs/server';
import { getBackendConfig } from '@/lib/backend';

// Proxy for the "Play it out" continuation's defender reply. Read-only by
// construction: the backend route never grades a move, never writes the
// trainer rating and never captures into the review queue, so a settled
// drill's recorded outcome cannot change here. Statuses pass through so the
// board can treat 503 (nothing written) as retryable.
export async function POST(request: NextRequest) {
  const { backendApiUrl, internalSecret } = getBackendConfig();
  const backendUrl = new URL('/api/endgames/playout/reply', backendApiUrl);

  try {
    const { userId } = await auth();
    if (!userId) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    const body = await request.json();

    const response = await fetch(backendUrl, {
      method: 'POST',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        'X-Internal-Secret': internalSecret,
        'X-Clerk-User-Id': userId,
      },
      body: JSON.stringify(body),
    });

    return new NextResponse(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: {
        'content-type': response.headers.get('content-type') ?? 'application/json',
      },
    });
  } catch (error) {
    console.error('Endgame playout reply proxy error:', error);
    return NextResponse.json(
      { error: 'Endgame backend is unreachable' },
      { status: 502 }
    );
  }
}
