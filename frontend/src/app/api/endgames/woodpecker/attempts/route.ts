import { NextRequest, NextResponse } from 'next/server';
import { auth } from '@clerk/nextjs/server';
import { getBackendConfig } from '@/lib/backend';

// Proxy for one graded move of an endgame review replay. Same per-move
// contract as /api/endgames/move (the rated trainer): the backend grades
// every legal move, generates the defender's reply past the stored line,
// and detects real resolution. The differences are the route's own: the
// body carries entry_id + accumulated time_taken_ms, the FSRS card is
// scheduled on the resolving move, and nothing touches the trainer rating.
// Status/detail pass through unchanged so the board can tell retryable
// (503/429/502) from client-integrity (400/404/409) failures.
export async function POST(request: NextRequest) {
  const { backendApiUrl, internalSecret } = getBackendConfig();
  const backendUrl = new URL(
    '/api/endgames/woodpecker/attempts',
    backendApiUrl
  );

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
    console.error('Endgame woodpecker attempts proxy error:', error);
    return NextResponse.json(
      { error: 'Endgame backend is unreachable' },
      { status: 502 }
    );
  }
}
