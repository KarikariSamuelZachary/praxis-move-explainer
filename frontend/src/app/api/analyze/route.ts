import { NextRequest, NextResponse } from 'next/server';
import { redis } from '@/lib/redis';

type AnalyzeRequest = {
  pgn?: string;
  target_color?: 'white' | 'black' | 'both';
};

type ParsedAnalyzePayload = {
  pgn: string;
  targetColor: 'white' | 'black' | 'both';
};

const MAX_REQUESTS_PER_WINDOW = 5;
const RATE_LIMIT_WINDOW_SECONDS = 60;
const MAX_PGN_BYTES = 2 * 1024 * 1024;
const ALLOWED_FILE_EXTENSIONS = ['.pgn', '.txt'];
const ALLOWED_FILE_MIME_TYPES = new Set([
  'application/x-chess-pgn',
  'application/vnd.chess-pgn',
  'text/plain',
  'application/octet-stream',
  '',
]);

function getClientIp(request: NextRequest): string {
  const forwardedFor = request.headers.get('x-forwarded-for');
  if (forwardedFor) {
    return forwardedFor.split(',')[0].trim();
  }

  return 'unknown';
}

async function isRateLimited(ip: string): Promise<boolean> {
  const key = `rate_limit:analyze:${ip}`;
  let count: number;
  try {
    count = await redis.incr(key);
  } catch (error) {
    // Fail-closed: if we cannot enforce the limit, deny the request outright.
    console.error('[analyze ratelimit] incr FAILED key=', key, error);
    return true;
  }

  if (count === 1) {
    try {
      await redis.expire(key, RATE_LIMIT_WINDOW_SECONDS);
    } catch (error) {
      console.error('[analyze ratelimit] expire FAILED key=', key, error);
    }
  }

  return count > MAX_REQUESTS_PER_WINDOW;
}

function isSupportedFileName(fileName: string): boolean {
  const lowerName = fileName.toLowerCase();
  return ALLOWED_FILE_EXTENSIONS.some((ext) => lowerName.endsWith(ext));
}

async function readUploadedPgn(entry: File): Promise<string> {
  if (entry.size > MAX_PGN_BYTES) {
    throw new Error(`PGN file exceeds the ${MAX_PGN_BYTES} byte limit`);
  }

  const fileMime = entry.type;
  if (fileMime && !ALLOWED_FILE_MIME_TYPES.has(fileMime)) {
    throw new Error(`Unsupported file type: ${fileMime}`);
  }

  const text = await entry.text();
  return text.trim();
}

async function parseAnalyzeRequest(
  request: NextRequest,
): Promise<ParsedAnalyzePayload | NextResponse> {
  const contentType = request.headers.get('content-type') ?? '';

  if (contentType.includes('multipart/form-data')) {
    let formData: FormData;
    try {
      formData = await request.formData();
    } catch {
      return NextResponse.json(
        { error: 'Invalid multipart payload' },
        { status: 400 }
      );
    }

    const fileEntry = formData.get('pgnFile');
    const targetColorValue = formData.get('target_color');
    const targetColor =
      targetColorValue === 'white' ||
      targetColorValue === 'black' ||
      targetColorValue === 'both'
        ? targetColorValue
        : 'both';

    if (!fileEntry) {
      return NextResponse.json(
        { error: 'Missing required field: pgnFile' },
        { status: 400 }
      );
    }

    if (!(fileEntry instanceof File)) {
      return NextResponse.json(
        { error: 'pgnFile must be a file upload' },
        { status: 400 }
      );
    }

    if (!isSupportedFileName(fileEntry.name)) {
      return NextResponse.json(
        { error: `Unsupported file extension. Allowed: ${ALLOWED_FILE_EXTENSIONS.join(', ')}` },
        { status: 400 }
      );
    }

    try {
      const pgn = await readUploadedPgn(fileEntry);
      if (!pgn) {
        return NextResponse.json(
          { error: 'Uploaded PGN file is empty' },
          { status: 400 }
        );
      }

      return { pgn, targetColor };
    } catch (error) {
      return NextResponse.json(
        {
          error: 'Failed to read uploaded PGN',
          detail: error instanceof Error ? error.message : 'Invalid file',
        },
        { status: 400 }
      );
    }
  }

  let body: AnalyzeRequest;
  try {
    body = (await request.json()) as AnalyzeRequest;
  } catch {
    return NextResponse.json(
      { error: 'Invalid JSON body' },
      { status: 400 }
    );
  }

  const pgn = body.pgn?.trim();
  if (!pgn) {
    return NextResponse.json(
      { error: 'Missing required field: pgn' },
      { status: 400 }
    );
  }

  if (pgn.length > MAX_PGN_BYTES) {
    return NextResponse.json(
      { error: 'PGN exceeds the maximum allowed size' },
      { status: 413 }
    );
  }

  const targetColor =
    body.target_color === 'white' ||
    body.target_color === 'black' ||
    body.target_color === 'both'
      ? body.target_color
      : 'both';

  return { pgn, targetColor };
}

export async function POST(request: NextRequest) {
  try {
    const parsed = await parseAnalyzeRequest(request);
    if (parsed instanceof NextResponse) {
      return parsed;
    }

    const { pgn, targetColor } = parsed;

    const ip = getClientIp(request);
    if (await isRateLimited(ip)) {
      return NextResponse.json(
        { detail: 'Too many requests. Please slow down.' },
        {
          status: 429,
          headers: { 'Retry-After': String(RATE_LIMIT_WINDOW_SECONDS) },
        }
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
        target_color: targetColor,
      }),
    });

    if (!response.ok) {
      const errorText = await response.text();
      console.error('FastAPI review endpoint failed:', {
        status: response.status,
        body: errorText,
      });
      return NextResponse.json(
        { error: 'Failed to analyze PGN', detail: 'The analysis service could not process this game.' },
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
        detail: 'An unexpected error occurred while analyzing the game.',
      },
      { status: 500 }
    );
  }
}