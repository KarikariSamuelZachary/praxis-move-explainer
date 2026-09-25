import { NextResponse } from 'next/server';
import { auth } from '@clerk/nextjs/server';
import { getBackendConfig } from '@/lib/backend';

// JSON proxy for the endgame recommendation. The Train page's card now
// resolves it server-side (lib/endgame-recommendation.ts) so it renders in
// the first paint; this route stays as the HTTP API, mirroring the
// backend's status codes (404 = the deployment has no drillable endgame
// content at all).
export async function GET() {
  const { backendApiUrl, internalSecret } = getBackendConfig();
  const backendUrl = new URL('/api/endgames/recommendation', backendApiUrl);

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
    console.error('Endgame recommendation proxy error:', error);
    return NextResponse.json(
      { error: 'Endgame backend is unreachable' },
      { status: 502 }
    );
  }
}
