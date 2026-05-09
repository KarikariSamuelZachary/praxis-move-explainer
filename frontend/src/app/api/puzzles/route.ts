import { NextRequest, NextResponse } from 'next/server';
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
  const key = `rate_limit:puzzles:${ip}`;
  const count = await redis.incr(key);

  if (count === 1) {
    await redis.expire(key, RATE_LIMIT_WINDOW_SECONDS);
  }

  return count > MAX_REQUESTS_PER_WINDOW;
}

export async function GET(request: NextRequest) {
  const backendApiUrl = process.env.BACKEND_API_URL ?? 'http://localhost:8000';
  const internalSecret = process.env.INTERNAL_SECRET ?? '';
  const backendUrl = new URL('/api/puzzles', backendApiUrl);
  backendUrl.search = request.nextUrl.search;

  try {
    const ip = getClientIp(request);
    if (await isRateLimited(ip)) {
      return NextResponse.json(
        { detail: 'Too many requests. Please slow down.' },
        { status: 429 }
      );
    }

    const response = await fetch(backendUrl, {
      headers: {
        Accept: request.headers.get('accept') ?? 'application/json',
        'X-Internal-Secret': internalSecret,
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
    console.error('Puzzle backend proxy error:', error);
    return NextResponse.json(
      { error: 'Puzzle backend is unreachable' },
      { status: 502 }
    );
  }
}
