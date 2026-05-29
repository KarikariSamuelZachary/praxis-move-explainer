import { NextRequest, NextResponse } from 'next/server';

type AnalyzeRequest = {
  pgn?: string;
  target_color?: 'white' | 'black' | 'both';
};

type AnalyzeErrorResponse = {
  detail?: string;
  error?: string;
};

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
