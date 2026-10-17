import { NextResponse } from 'next/server';
import { auth } from '@clerk/nextjs/server';
import { getBackendConfig } from '@/lib/backend';

// Proxy for grading one calc review replay. The client sends the review
// token from /queue plus the entered moves; the body passes through
// byte-for-byte. Responses pass through unchanged, including the failure
// reveal; error statuses (400 bad token, 404 entry, 409 replay, 422
// malformed) pass through so the page can react to each.
export async function POST(request: Request) {
  const { backendApiUrl, internalSecret } = getBackendConfig();
  const backendUrl = new URL('/api/calc/woodpecker/attempt', backendApiUrl);

  try {
    const { userId } = await auth();
    if (!userId) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    const body = await request.json().catch(() => null);
    const response = await fetch(backendUrl, {
      method: 'POST',
      cache: 'no-store',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        'X-Internal-Secret': internalSecret,
        'X-Clerk-User-Id': userId,
      },
      body: JSON.stringify(body),
    });

    return new NextResponse(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: {
        'content-type': response.headers.get('content-type') ?? 'application/json',
      },
    });
  } catch (error) {
    console.error('Calc woodpecker attempt proxy error:', error);
    return NextResponse.json(
      { error: 'Calculation backend is unreachable' },
      { status: 502 }
    );
  }
}
