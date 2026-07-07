import { NextRequest, NextResponse } from 'next/server';
import { redis } from '@/lib/redis';

type AnalyzeRequest = {
  pgn?: string;
  target_color?: 'white' | 'black' | 'both';
};

type AnalyzeErrorResponse = {
  detail?: string;
  error?: string;
};

const MAX_REQUESTS_PER_WINDOW = 5;
const RATE_LIMIT_WINDOW_SECONDS = 60;

function getClientIp(request: NextRequest): string {
  const forwardedFor = request.headers.get('x-forwarded-for');
  if (forwardedFor) {
    return forwardedFor.split(',')[0].trim();
  }

  return 'unknown';
}

async function isRateLimited(ip: string): Promise<boolean> {
  const key = `rate_limit:analyze:${ip}`;
  const count = await redis.incr(key);

  if (count === 1) {
    await redis.expire(key, RATE_LIMIT_WINDOW_SECONDS);
  }

  return count > MAX_REQUESTS_PER_WINDOW;
}

export async function POST(request: NextRequest) {
  try {
    const body = (await request.json()) as AnalyzeRequest;
    const pgn = body.pgn?.trim();

    if (!pgn) {
      return NextResponse.json(
        { error: 'Missing required field: pgn' },
        { status: 400 }
      );
    }

    const ip = getClientIp(request);
    if (await isRateLimited(ip)) {
      return NextResponse.json(
        { detail: 'Too many requests. Please slow down.' },
        { status: 429 }
      );
    }

    const backendApiUrl = process.env.BACKEND_API_URL ?? 'http://localhost:8000';
    const internalSecret = process.env.INTERNAL_SECRET ?? '';
    const backendUrl = new URL('/api/review', backendApiUrl);

    const response = await fetch(backendUrl, {
      method: 'POST',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        'X-Internal-Secret': internalSecret,
      },
      body: JSON.stringify({
        pgn,
        target_color: body.target_color ?? 'both',
      }),
    });

    if (!response.ok) {
      const errorText = await response.text();
      let errorDetail = errorText;
      try {
        const parsedError = JSON.parse(errorText) as AnalyzeErrorResponse;
        errorDetail = parsedError.detail ?? parsedError.error ?? errorText;
      } catch {
        // Keep the raw body for non-JSON backend errors.
      }

      console.error('FastAPI review endpoint failed:', {
        status: response.status,
        body: errorText,
      });
      return NextResponse.json(
        { error: 'Failed to analyze PGN', detail: errorDetail },
        { status: response.status }
      );
    }

    return new NextResponse(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: {
        'content-type': response.headers.get('content-type') ?? 'application/json',
      },
    });
  } catch (error) {
    console.error('Analyze API route error:', error);
    return NextResponse.json(
      {
        error: 'Failed to analyze PGN',
        detail: error instanceof Error ? error.message : 'Unexpected analyze API error',
      },
      { status: 500 }
    );
  }
}
