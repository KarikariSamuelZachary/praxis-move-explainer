import { NextRequest, NextResponse } from 'next/server';
import { auth } from '@clerk/nextjs/server';

/**
 * /api/repertoires/positions/{position_id} - proxy to the FastAPI
 * backend.
 *
 * Two methods share this URL:
 *
 *   * DELETE - the detail page's "My saved moves" trash icon calls
 *     this to remove a single stored position. The backend enforces
 *     the 404/403 ownership pattern (via _load_owned_position_for_update,
 *     the same JOIN-to-repertoires pre-check review_position uses)
 *     and a 409 if the target has prepared responses beneath it
 *     (would orphan the descendant subtree). The 409 detail message
 *     is forwarded verbatim so the client can surface it as the
 *     user-facing prompt.
 *
 *   * POST /review - the Train flow calls this once per position
 *     during a session with `{solved_correctly: bool, time_taken_ms:
 *     number}`. The endpoint is NOT session-aware (the client tallies
 *     `positions_correct` itself across /review responses and reports
 *     the total to /sessions/{session_id}/complete); it persists the
 *     FSRS state transition and returns the new state + scheduling
 *     envelope so the client can confirm the persistence happened.
 *     Both methods are kept here because the backend keys both by the
 *     same `{position_id}` path segment.
 *
 * No GET/PUT here - that surface lives at the sibling
 * /api/repertoires/{id}/positions route, which is keyed by repertoire
 * id, not by position id.
 *
 * Pattern parity with src/app/api/repertoires/[id]/route.ts and
 * src/app/api/repertoires/[id]/positions/route.ts: resolve
 * BACKEND_API_URL, forward X-Internal-Secret + X-Clerk-User-Id,
 * stream the response body back with the same status / content-type
 * so the client's `body.detail || body.error` parsing works for both
 * the 200 success body and the 409 detail string.
 *
 * Next.js 16: `params` is a Promise to await.
 */

const BACKEND_API_URL = process.env.BACKEND_API_URL ?? 'http://localhost:8000';
const INTERNAL_SECRET = process.env.INTERNAL_SECRET ?? '';

type RouteContext = { params: Promise<{ position_id: string }> };

export async function DELETE(_request: NextRequest, context: RouteContext) {
  const { position_id } = await context.params;
  const backendUrl = new URL(
    `/api/repertoires/positions/${encodeURIComponent(position_id)}`,
    BACKEND_API_URL
  );

  try {
    const { userId } = await auth();
    if (!userId) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    const response = await fetch(backendUrl, {
      method: 'DELETE',
      headers: {
        Accept: 'application/json',
        'X-Internal-Secret': INTERNAL_SECRET,
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
    console.error('Repertoire position delete proxy error:', error);
    return NextResponse.json(
      { error: 'Backend is unreachable' },
      { status: 502 }
    );
  }
}

export async function POST(request: NextRequest, context: RouteContext) {
  const { position_id } = await context.params;
  const backendUrl = new URL(
    `/api/repertoires/positions/${encodeURIComponent(position_id)}/review`,
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
    console.error('Repertoire position review proxy error:', error);
    return NextResponse.json(
      { error: 'Backend is unreachable' },
      { status: 502 }
    );
  }
}
