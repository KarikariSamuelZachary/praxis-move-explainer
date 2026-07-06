import { NextRequest, NextResponse } from 'next/server';
import { auth } from '@clerk/nextjs/server';

export async function GET(request: NextRequest) {
  try {
    const { userId } = await auth();
    if (!userId) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    const backendApiUrl = process.env.BACKEND_API_URL ?? 'http://localhost:8000';
    const internalSecret = process.env.INTERNAL_SECRET ?? '';
    const backendUrl = new URL('/api/user/rating', backendApiUrl);

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
        'content-type': response.headers.get('content-type') ?? 'application/json',
      },
    });
  } catch (error) {
    console.error('User rating GET proxy error:', error);
    return NextResponse.json(
      { error: 'Backend unreachable' },
      { status: 502 },
    );
  }
}