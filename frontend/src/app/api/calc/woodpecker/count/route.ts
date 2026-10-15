import { NextResponse } from 'next/server';
import { auth } from '@clerk/nextjs/server';
import { getBackendConfig } from '@/lib/backend';

// Proxy for the calc Woodpecker due-count badge. Read-only.
export async function GET() {
  const { backendApiUrl, internalSecret } = getBackendConfig();
  const backendUrl = new URL('/api/calc/woodpecker/count', backendApiUrl);

  try {
    const { userId } = await auth();
    if (!userId) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

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
    console.error('Calc woodpecker count proxy error:', error);
    return NextResponse.json(
      { error: 'Calculation backend is unreachable' },
      { status: 502 }
    );
  }
}
