import { NextRequest, NextResponse } from 'next/server';
import { auth } from '@clerk/nextjs/server';
import { redis } from '@/lib/redis';

const MAX_REQUESTS_PER_WINDOW = 60;
const RATE_LIMIT_WINDOW_SECONDS = 60;

function getClientIp(request: NextRequest): string {
  const forwardedFor = request.headers.get('x-forwarded-for');
  if (forwardedFor) {
    return forwardedFor.split(',')[0].trim();
  }
  return 'unknown';
}

async function isRateLimited(ip: string): Promise<boolean> {
  const key = `rate_limit:puzzles_by_id:${ip}`;
  const count = await redis.incr(key);
  if (count === 1) {
    await redis.expire(key, RATE_LIMIT_WINDOW_SECONDS);
  }
  return count > MAX_REQUESTS_PER_WINDOW;
}

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const backendApiUrl = process.env.BACKEND_API_URL ?? 'http://localhost:8000';
  const internalSecret = process.env.INTERNAL_SECRET ?? '';
  const requestPath = request.nextUrl.pathname;

  try {
    const authStartedAt = performance.now();
    const { userId } = await auth();
    console.info('[WOODPECKER_PROFILE] phase=proxy_auth', {
      timestamp: new Date().toISOString(),
      path: requestPath,
      elapsed_ms: Number((performance.now() - authStartedAt).toFixed(2)),
      user_present: Boolean(userId),
    });
    if (!userId) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    const { id } = await params;
    const ip = getClientIp(request);
    const rateLimitStartedAt = performance.now();
    const rateLimited = await isRateLimited(ip);
    console.info('[WOODPECKER_PROFILE] phase=proxy_redis_rate_limit', {
      timestamp: new Date().toISOString(),
      path: requestPath,
      elapsed_ms: Number((performance.now() - rateLimitStartedAt).toFixed(2)),
      rate_limited: rateLimited,
    });
    if (rateLimited) {
      return NextResponse.json(
        { detail: 'Too many requests. Please slow down.' },
        { status: 429 }
      );
    }

    const backendUrl = new URL(`/api/puzzles/${encodeURIComponent(id)}`, backendApiUrl);

    const backendStartedAt = performance.now();
    const response = await fetch(backendUrl, {
      headers: {
        Accept: 'application/json',
        'X-Internal-Secret': internalSecret,
        'X-Clerk-User-Id': userId,
      },
    });
    console.info('[WOODPECKER_PROFILE] phase=proxy_backend_fetch', {
      timestamp: new Date().toISOString(),
      path: requestPath,
      elapsed_ms: Number((performance.now() - backendStartedAt).toFixed(2)),
      status: response.status,
    });

    return new NextResponse(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: {
        'content-type': response.headers.get('content-type') ?? 'application/json',
      },
    });
  } catch (error) {
    console.error('Puzzle-by-id proxy error:', error);
    return NextResponse.json(
      { error: 'Puzzle backend is unreachable' },
      { status: 502 }
    );
  }
}
