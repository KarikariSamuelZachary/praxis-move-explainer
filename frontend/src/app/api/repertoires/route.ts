import { NextRequest, NextResponse } from 'next/server';
import { auth } from '@clerk/nextjs/server';

/**
 * /api/repertoires - thin proxy to the FastAPI backend.
 *
 * Pattern parity with the rest of the Next.js route handlers
 * (src/app/api/woodpecker/{entries,queue,attempts}/route.ts and the
 * train / puzzles / user / import siblings):
 *   * Resolve the backend URL from BACKEND_API_URL (default
 *     http://localhost:8000) and forward `X-Internal-Secret` +
 *     `X-Clerk-User-Id` on every request - the FastAPI routers read
 *     the latter via their `_get_user_id(request)` helper.
 *   * Pass the request body through verbatim for POST.
 *   * Stream the response body back with the same status, statusText,
 *     and content-type so error `{detail,error}` shapes land on the
 *     client untouched (the page can show body.detail || body.error).
 *   * 401 if Clerk reports no user; 502 if the backend is unreachable.
 */

const BACKEND_API_URL = process.env.BACKEND_API_URL ?? 'http://localhost:8000';
const INTERNAL_SECRET = process.env.INTERNAL_SECRET ?? '';

export async function GET(_request: NextRequest) {
  const backendUrl = new URL('/api/repertoires', BACKEND_API_URL);

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
    console.error('Repertoires list proxy error:', error);
    return NextResponse.json(
      { error: 'Backend is unreachable' },
      { status: 502 }
    );
  }
}

export async function POST(request: NextRequest) {
  const backendUrl = new URL('/api/repertoires', BACKEND_API_URL);

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
    console.error('Repertoire create proxy error:', error);
    return NextResponse.json(
      { error: 'Backend is unreachable' },
      { status: 502 }
    );
  }
}
