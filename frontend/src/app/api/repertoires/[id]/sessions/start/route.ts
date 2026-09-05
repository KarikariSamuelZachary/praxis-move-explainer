import { NextRequest, NextResponse } from 'next/server';
import { auth } from '@clerk/nextjs/server';

/**
 * /api/repertoires/{id}/sessions/start - proxy to the FastAPI backend.
 *
 * The Train flow's config modal posts here when the user confirms
 * their scope selection. The backend returns the freshly-inserted
 * `repertoire_training_sessions` row plus the list of
 * `RepertoirePosition` rows the client should now present in order
 * (a server-side mutation - this is NOT a preview endpoint, so the
 * modal must not call it just to display a count).
 *
 * Empty-set handling lives at the source: the backend returns
 * 400 "no positions to train for this mode" with no row inserted
 * when the selector resolves to an empty set. The proxy streams the
 * 400 detail verbatim so the modal can render it inline ("nothing to
 * train"), rather than navigating to an empty quiz screen.
 *
 * Pattern parity with src/app/api/repertoires/positions/[position_id]/route.ts:
 * resolve BACKEND_API_URL, forward X-Internal-Secret + X-Clerk-User-Id,
 * stream the response body back with the same status / content-type so
 * the client's `body.detail || body.error` parsing works for both
 * the 200 envelope and the 400 detail string.
 *
 * Next.js 16: `params` is a Promise to await.
 */

const BACKEND_API_URL = process.env.BACKEND_API_URL ?? 'http://localhost:8000';
const INTERNAL_SECRET = process.env.INTERNAL_SECRET ?? '';

type RouteContext = { params: Promise<{ id: string }> };

export async function POST(request: NextRequest, context: RouteContext) {
  const { id } = await context.params;
  const backendUrl = new URL(
    `/api/repertoires/${encodeURIComponent(id)}/sessions/start`,
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
    console.error('Repertoire session start proxy error:', error);
    return NextResponse.json(
      { error: 'Backend is unreachable' },
      { status: 502 }
    );
  }
}
