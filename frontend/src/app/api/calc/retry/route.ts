import { NextResponse } from 'next/server';
import { auth } from '@clerk/nextjs/server';
import { getBackendConfig } from '@/lib/backend';

// Proxy for the Blind Calculation trainer's retry endpoint. Swaps a (possibly
// spent) exercise token for a fresh one bound to the same segment/offset/n,
// so a practice retry can be graded again. The retry submit stores unrated
// server-side via the first-ever rule.
export async function POST(request: Request) {
  const { backendApiUrl, internalSecret } = getBackendConfig();
  const backendUrl = new URL('/api/calc/retry', backendApiUrl);

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
    console.error('Calc retry proxy error:', error);
    return NextResponse.json(
      { error: 'Calculation backend is unreachable' },
      { status: 502 }
    );
  }
}
