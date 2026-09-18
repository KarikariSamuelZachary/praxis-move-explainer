import { NextRequest, NextResponse } from 'next/server';
import { auth } from '@clerk/nextjs/server';
import { getBackendConfig } from '@/lib/backend';

/**
 * /api/repertoires/suggestions - proxy to the FastAPI backend's
 * Stockfish move-suggestion endpoint.
 *
 * GET with a `fen` query parameter. The backend runs Stockfish multi-PV
 * on that position and returns a ranked list of the side-to-move's best
 * legal moves (`{uci, san, score_cp}`), which the repertoire build page
 * renders in its "Other moves" panel.
 *
 * Pattern parity with the sibling repertoire proxy routes: resolve
 * BACKEND_API_URL, forward X-Internal-Secret + X-Clerk-User-Id, stream
 * the response body back with the same status / content-type so the
 * client's `body.detail || body.error` parsing works.
 */

const { backendApiUrl: BACKEND_API_URL, internalSecret: INTERNAL_SECRET } = getBackendConfig();

// One Stockfish multi-PV analysis upstream (~0.4s nominal); anything close
// to this bound means the engine is stuck.
const SUGGESTIONS_TIMEOUT_MS = 30_000;

export async function GET(request: NextRequest) {
  const fen = request.nextUrl.searchParams.get('fen');

  if (!fen) {
    return NextResponse.json({ error: 'Missing fen' }, { status: 400 });
  }

  const backendUrl = new URL('/api/repertoires/suggestions', BACKEND_API_URL);
  backendUrl.searchParams.set('fen', fen);

  try {
    const { userId } = await auth();
    if (!userId) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    const response = await fetch(backendUrl, {
      signal: AbortSignal.timeout(SUGGESTIONS_TIMEOUT_MS),
      headers: {
        Accept: 'application/json',
        'X-Internal-Secret': INTERNAL_SECRET,
        'X-Clerk-User-Id': userId,
      },
      cache: 'no-store',
    });

    return new NextResponse(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: {
        'content-type': response.headers.get('content-type') ?? 'application/json',
      },
    });
  } catch (error) {
    if (error instanceof Error && error.name === 'TimeoutError') {
      return NextResponse.json(
        { error: 'Suggestion request timed out', detail: 'The engine took too long. Please try again.' },
        { status: 504 }
      );
    }
    console.error('Repertoire suggestions proxy error:', error);
    return NextResponse.json(
      { error: 'Backend is unreachable' },
      { status: 502 }
    );
  }
}
