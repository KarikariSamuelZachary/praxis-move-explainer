import { NextRequest, NextResponse } from 'next/server';
import { auth } from '@clerk/nextjs/server';

export async function POST(request: NextRequest) {
  const requestStarted = performance.now();
  const backendApiUrl = process.env.BACKEND_API_URL ?? 'http://localhost:8000';
  const internalSecret = process.env.INTERNAL_SECRET ?? '';
  const backendUrl = new URL('/api/woodpecker/entries', backendApiUrl);

  try {
    const authStarted = performance.now();
    const { userId } = await auth();
    console.info('[WOODPECKER_ENTRY_PROFILE] proxy_auth_resolved', {
      timestamp: new Date().toISOString(),
      duration_ms: Number((performance.now() - authStarted).toFixed(2)),
      authenticated: Boolean(userId),
    });
    if (!userId) {
      console.info('[WOODPECKER_ENTRY_PROFILE] proxy_response', {
        status: 401,
        duration_ms: Number((performance.now() - requestStarted).toFixed(2)),
      });
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    const body = await request.json();

    const backendStarted = performance.now();
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

    console.info('[WOODPECKER_ENTRY_PROFILE] proxy_backend_resolved', {
      timestamp: new Date().toISOString(),
      puzzle_id: body?.puzzle_id,
      status: response.status,
      ok: response.ok,
      backend_duration_ms: Number((performance.now() - backendStarted).toFixed(2)),
      total_duration_ms: Number((performance.now() - requestStarted).toFixed(2)),
    });

    return new NextResponse(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: {
        'content-type': response.headers.get('content-type') ?? 'application/json',
      },
    });
  } catch (error) {
    console.error('[WOODPECKER_ENTRY_PROFILE] proxy_error', {
      duration_ms: Number((performance.now() - requestStarted).toFixed(2)),
      error,
    });
    return NextResponse.json(
      { error: 'Backend is unreachable' },
      { status: 502 }
    );
  }
}
