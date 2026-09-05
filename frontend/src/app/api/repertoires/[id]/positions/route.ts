import { NextRequest, NextResponse } from 'next/server';
import { auth } from '@clerk/nextjs/server';

/**
 * /api/repertoires/{id}/positions - proxy to the FastAPI backend.
 *
 * Same path serves GET (list stored positions, for the detail page's
 * "My saved moves" hydration) and POST (persist a drag-and-drop move)
 * - Next.js dispatches by method just like FastAPI does server-side.
 *
 * The backend has parallel methods: GET /api/repertoires/{id}/positions
 * (added alongside the existing POST for the detail page fix; reads
 * every stored row without filtering by `due` - that's for /queue)
 * and POST /api/repertoires/{id}/positions (pre-existing upsert).
 *
 * Pattern parity with src/app/api/woodpecker/{entries,queue,attempts}/route.ts:
 * resolve BACKEND_API_URL, forward X-Internal-Secret + X-Clerk-User-Id,
 * stream the response body back with the same status / content-type so
 * the client's `body.detail || body.error` parsing works.
 *
 * Next.js 16: `params` is a Promise to await.
 */

const BACKEND_API_URL = process.env.BACKEND_API_URL ?? 'http://localhost:8000';
const INTERNAL_SECRET = process.env.INTERNAL_SECRET ?? '';

type RouteContext = { params: Promise<{ id: string }> };

export async function GET(_request: NextRequest, context: RouteContext) {
  const { id } = await context.params;
  const backendUrl = new URL(
    `/api/repertoires/${encodeURIComponent(id)}/positions`,
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
    console.error('Repertoire positions list proxy error:', error);
    return NextResponse.json(
      { error: 'Backend is unreachable' },
      { status: 502 }
    );
  }
}

export async function POST(request: NextRequest, context: RouteContext) {
  const { id } = await context.params;
  const backendUrl = new URL(
    `/api/repertoires/${encodeURIComponent(id)}/positions`,
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
    console.error('Repertoire positions upsert proxy error:', error);
    return NextResponse.json(
      { error: 'Backend is unreachable' },
      { status: 502 }
    );
  }
}
