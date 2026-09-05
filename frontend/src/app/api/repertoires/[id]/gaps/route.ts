import { NextRequest, NextResponse } from 'next/server';
import { auth } from '@clerk/nextjs/server';

/**
 * GET /api/repertoires/{id}/gaps - proxy to the FastAPI backend.
 *
 * Pattern parity with src/app/api/woodpecker/{entries,queue,attempts}/route.ts:
 * forward X-Internal-Secret + X-Clerk-User-Id, stream the response
 * body back with the same status / content-type so the client's
 * `body.detail || body.error` parsing works.
 *
 * IMPORTANT: the upstream `/gaps` endpoint analyzes the WHOLE
 * repertoire in one call (it hits Lichess Explorer per stored
 * position) - the page mounts this ONCE on load and filters the
 * cached gaps locally per navigation. Do not refactor this into a
 * per-position variant; see the page's "Other moves" section for
 * the rationale.
 *
 * Next.js 16: `params` is a Promise to await.
 */

const BACKEND_API_URL = process.env.BACKEND_API_URL ?? 'http://localhost:8000';
const INTERNAL_SECRET = process.env.INTERNAL_SECRET ?? '';

type RouteContext = { params: Promise<{ id: string }> };

export async function GET(_request: NextRequest, context: RouteContext) {
  const { id } = await context.params;
  const backendUrl = new URL(
    `/api/repertoires/${encodeURIComponent(id)}/gaps`,
    BACKEND_API_URL
  );

  try {
    const { userId } = await auth();
    if (!userId) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    const response = await fetch(backendUrl, {
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
    console.error('Repertoire gaps proxy error:', error);
    return NextResponse.json(
      { error: 'Backend is unreachable' },
      { status: 502 }
    );
  }
}
