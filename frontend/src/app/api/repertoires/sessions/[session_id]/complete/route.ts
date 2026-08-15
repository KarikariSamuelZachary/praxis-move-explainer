import { NextRequest, NextResponse } from 'next/server';
import { auth } from '@clerk/nextjs/server';

/**
 * /api/repertoires/sessions/{session_id}/complete — proxy to the FastAPI
 * backend.
 *
 * The Train flow posts here once, after the last position in the
 * session has been reviewed, with `{positions_correct: number}`. The
 * backend sets `completed_at = NOW()` and `positions_correct` on the
 * session row (validated 0..=positions_total server-side) and returns
 * the updated `RepertoireTrainingSession` envelope.
 *
 * Pattern parity with src/app/api/repertoires/positions/[position_id]/route.ts:
 * resolve BACKEND_API_URL, forward X-Internal-Secret + X-Clerk-User-Id,
 * stream the response body back with the same status / content-type so
 * the client's `body.detail || body.error` parsing works for both the
 * 200 success body and the 400 detail string (e.g. "training session
 * already completed" if a second /complete races the first).
 *
 * Next.js 16: `params` is a Promise to await.
 */

const BACKEND_API_URL = process.env.BACKEND_API_URL ?? 'http://localhost:8000';
const INTERNAL_SECRET = process.env.INTERNAL_SECRET ?? '';

type RouteContext = { params: Promise<{ session_id: string }> };

export async function POST(request: NextRequest, context: RouteContext) {
  const { session_id } = await context.params;
  const backendUrl = new URL(
    `/api/repertoires/sessions/${encodeURIComponent(session_id)}/complete`,
    BACKEND_API_URL
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
        'X-Internal-Secret': INTERNAL_SECRET,
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
    console.error('Repertoire session complete proxy error:', error);
    return NextResponse.json(
      { error: 'Backend is unreachable' },
      { status: 502 }
    );
  }
}
