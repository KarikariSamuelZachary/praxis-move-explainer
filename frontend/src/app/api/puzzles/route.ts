import { NextRequest, NextResponse } from 'next/server';

export async function GET(request: NextRequest) {
  const backendApiUrl = process.env.BACKEND_API_URL ?? 'http://localhost:8000';
  const backendUrl = new URL('/api/puzzles', backendApiUrl);
  backendUrl.search = request.nextUrl.search;

  try {
    const response = await fetch(backendUrl, {
      headers: {
        Accept: request.headers.get('accept') ?? 'application/json',
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
