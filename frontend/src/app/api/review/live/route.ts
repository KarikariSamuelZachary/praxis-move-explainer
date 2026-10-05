import { NextRequest, NextResponse } from 'next/server';
import { auth } from '@clerk/nextjs/server';

import { getBackendConfig } from '@/lib/backend';

const LIVE_TIMEOUT_MS = 30_000;

type LiveRequestBody = {
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

  let body: LiveRequestBody;
  try {
    body = (await request.json()) as LiveRequestBody;
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
  const response = await fetch(new URL('/api/review/live', backendApiUrl), {
    method: 'POST',
    signal: AbortSignal.timeout(LIVE_TIMEOUT_MS),
    headers: {
      Accept: 'application/json',
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

  const text = await response.text();
  if (!response.ok) {
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

  return new NextResponse(text, {
    status: response.status,
    headers: { 'content-type': 'application/json' },
  });
}
