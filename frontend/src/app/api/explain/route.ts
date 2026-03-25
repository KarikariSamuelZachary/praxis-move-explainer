import { NextRequest, NextResponse } from 'next/server';
import { getCachedExplanation } from '@/lib/groq';
import { ExplanationRequest } from '@/types';

const MAX_BODY_BYTES = 4 * 1024;
const MAX_REQUESTS_PER_WINDOW = 30;
const RATE_LIMIT_WINDOW_MS = 60_000;
const rateLimitStore = new Map<string, { count: number; resetAt: number }>();

function getClientIp(request: NextRequest): string {
  const forwardedFor = request.headers.get('x-forwarded-for');
  if (forwardedFor) {
    return forwardedFor.split(',')[0].trim();
  }

  return request.headers.get('x-real-ip') ?? 'unknown';
}

function isRateLimited(ip: string): boolean {
  const now = Date.now();
  const entry = rateLimitStore.get(ip);

  if (!entry || entry.resetAt <= now) {
    rateLimitStore.set(ip, {
      count: 1,
      resetAt: now + RATE_LIMIT_WINDOW_MS,
    });
    return false;
  }

  if (entry.count >= MAX_REQUESTS_PER_WINDOW) {
    return true;
  }

  entry.count += 1;
  return false;
}

function isValidFen(fen: string): boolean {
  const parts = fen.trim().split(/\s+/);
  return parts.length === 6;
}

function isValidUciMove(move: string): boolean {
  return /^[a-h][1-8][a-h][1-8][nbrq]?$/i.test(move.trim());
}

function sanitizeRequest(body: Partial<ExplanationRequest>): ExplanationRequest | null {
  const fen = body.fen?.trim();
  const move = body.move?.trim().toLowerCase();

  if (!fen || !move || !isValidFen(fen) || !isValidUciMove(move)) {
    return null;
  }

  return {
    fen,
    move,
    isCorrect: body.isCorrect ?? true,
    playerElo: Math.min(Math.max(body.playerElo ?? 1200, 100), 3200),
    puzzleThemes: Array.isArray(body.puzzleThemes)
      ? body.puzzleThemes
          .filter((theme): theme is string => typeof theme === 'string')
          .map((theme) => theme.trim())
          .filter(Boolean)
          .slice(0, 10)
      : [],
  };
}

export async function POST(request: NextRequest) {
  try {
    const contentLengthHeader = request.headers.get('content-length');
    const contentLength = contentLengthHeader ? Number(contentLengthHeader) : 0;
    if (contentLengthHeader && (!Number.isFinite(contentLength) || contentLength > MAX_BODY_BYTES)) {
      return NextResponse.json({ error: 'Request body too large' }, { status: 413 });
    }

    const ip = getClientIp(request);
    if (isRateLimited(ip)) {
      return NextResponse.json({ error: 'Too many requests' }, { status: 429 });
    }

    const body = (await request.json()) as Partial<ExplanationRequest>;

    const explanationRequest = sanitizeRequest(body);
    if (!explanationRequest) {
      return NextResponse.json(
        { error: 'Invalid request payload' },
        { status: 400 }
      );
    }

    const explanation = await getCachedExplanation(explanationRequest);

    return NextResponse.json(explanation);
  } catch (error) {
    console.error('Explanation API error:', error);
    return NextResponse.json(
      {
        explanation: 'This move creates a decisive tactical advantage.',
        concept: 'Tactics',
        tip: 'Always calculate forcing moves first: checks, captures, and threats.',
      },
      { status: 200 } // Return fallback instead of error for better UX
    );
  }
}
