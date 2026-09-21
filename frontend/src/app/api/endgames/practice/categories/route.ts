import { NextResponse } from 'next/server';
import { auth } from '@clerk/nextjs/server';
import { getBackendConfig } from '@/lib/backend';

// Proxy for the practice picker's category list. The backend builds it from
// the DB (sourced positions only), so only categories that can actually hand
// back a position are returned -- the picker renders exactly this array.
export async function GET() {
  const { backendApiUrl, internalSecret } = getBackendConfig();
  const backendUrl = new URL('/api/endgames/practice/categories', backendApiUrl);

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
    console.error('Endgame practice categories proxy error:', error);
    return NextResponse.json(
      { error: 'Endgame backend is unreachable' },
      { status: 502 }
    );
  }
}
