import { NextResponse } from 'next/server';
import { auth } from '@clerk/nextjs/server';
import { getBackendConfig } from '@/lib/backend';

// Proxy for the Blind Calculation trainer's "serve one exercise" endpoint.
// The backend resolves the rating window from the user's calc rating at the
// requested depth and 404s when the window is empty; status and body pass
// through unchanged so the page can distinguish "nothing in range" from a
// transient failure. The response carries a FEN and a bearer token -- never
// the line.
export async function GET(request: Request) {
  const { backendApiUrl, internalSecret } = getBackendConfig();
  const { searchParams } = new URL(request.url);
  const backendUrl = new URL('/api/calc/next', backendApiUrl);
  const n = searchParams.get('n');
  if (n) backendUrl.searchParams.set('n', n);

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
    console.error('Calc next proxy error:', error);
    return NextResponse.json(
      { error: 'Calculation backend is unreachable' },
      { status: 502 }
    );
  }
}
