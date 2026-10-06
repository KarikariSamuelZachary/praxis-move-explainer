import { NextRequest, NextResponse } from 'next/server';
import { auth } from '@clerk/nextjs/server';

import { getBackendConfig } from '@/lib/backend';

const STREAM_TIMEOUT_MS = 60_000;

type StreamRequestBody = {
  moves?: string[];
  move?: string;
  player_rating?: number | null;
  expected_mode?: string | null;
};

export async function POST(request: NextRequest) {
  const { userId } = await auth();
  if (!userId) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }

  let body: StreamRequestBody;
  try {
    body = (await request.json()) as StreamRequestBody;
  } catch {
    return NextResponse.json({ error: 'Invalid JSON body' }, { status: 400 });
  }
  if (!body.move || (body.moves !== undefined && !Array.isArray(body.moves))) {
    return NextResponse.json(
      { error: 'Missing required fields: move, moves' },
      { status: 400 },
    );
  }

  const { backendApiUrl, internalSecret } = getBackendConfig();
  const response = await fetch(new URL('/api/review/live/stream', backendApiUrl), {
    method: 'POST',
    signal: AbortSignal.timeout(STREAM_TIMEOUT_MS),
    headers: {
      Accept: 'application/x-ndjson',
      'Content-Type': 'application/json',
      'X-Internal-Secret': internalSecret,
      'X-Clerk-User-Id': userId,
    },
    body: JSON.stringify({
      moves: body.moves ?? [],
      move: body.move,
      player_rating: body.player_rating ?? null,
      expected_mode: body.expected_mode ?? null,
    }),
  });

  if (!response.ok) {
    const text = await response.text();
    let detail = 'The analysis service could not analyze this move.';
    try {
      const parsed = JSON.parse(text) as { detail?: unknown };
      if (typeof parsed.detail === 'string' && parsed.detail.trim()) {
        detail = parsed.detail;
      }
    } catch {
      // Not JSON; keep the generic message.
    }
    return NextResponse.json({ error: detail, detail }, { status: response.status });
  }

  // Pipe the NDJSON body through untouched so per-depth lines reach the
  // client as they are produced (no buffering at this layer).
  return new NextResponse(response.body, {
    status: response.status,
    headers: {
      'content-type':
        response.headers.get('content-type') ?? 'application/x-ndjson',
      'cache-control': 'no-store',
    },
  });
}
