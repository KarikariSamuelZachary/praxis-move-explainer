import { NextRequest, NextResponse } from 'next/server';
import { auth } from '@clerk/nextjs/server';

const MAX_LIMIT = 50;
const DEFAULT_LIMIT = 10;

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ username: string }> }
) {
  try {
    const { userId } = await auth();
    if (!userId) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    const { username } = await params;

    const limitParam = Number.parseInt(
      request.nextUrl.searchParams.get('limit') ?? String(DEFAULT_LIMIT),
      10
    );
    const limit =
      Number.isFinite(limitParam) && limitParam >= 1 && limitParam <= MAX_LIMIT
        ? limitParam
        : DEFAULT_LIMIT;

    const backendApiUrl = process.env.BACKEND_API_URL ?? 'http://localhost:8000';
    const internalSecret = process.env.INTERNAL_SECRET ?? '';
    const backendUrl = new URL(
      `/api/import/chesscom/${encodeURIComponent(username)}`,
      backendApiUrl
    );
    backendUrl.searchParams.set('limit', String(limit));

    const response = await fetch(backendUrl, {
      cache: 'no-store',
      headers: {
        Accept: 'application/json',
        'X-Internal-Secret': internalSecret,
        'X-Clerk-User-Id': userId,
      },
    });

    return new NextResponse(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: {
        'content-type':
          response.headers.get('content-type') ?? 'application/json',
      },
    });
  } catch (error) {
    console.error('Chess.com import proxy error:', error);
    return NextResponse.json(
      { error: 'Backend unreachable' },
      { status: 502 }
    );
  }
}