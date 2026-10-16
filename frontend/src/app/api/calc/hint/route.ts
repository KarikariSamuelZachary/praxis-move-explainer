import { NextResponse } from 'next/server';
import { auth } from '@clerk/nextjs/server';
import { getBackendConfig } from '@/lib/backend';

// Proxy for the Blind Calculation trainer's single-ply hint endpoint. The
// client is blind by design (it never holds the line), so even the hint
// highlight comes from the server: {exercise_token, plies_entered} in,
// the single next expected UCI out. Writes nothing server-side; the client
// reports hints_used at submit and a hinted attempt stores unrated.
export async function POST(request: Request) {
  const { backendApiUrl, internalSecret } = getBackendConfig();
  const backendUrl = new URL('/api/calc/hint', backendApiUrl);

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
    console.error('Calc hint proxy error:', error);
    return NextResponse.json(
      { error: 'Calculation backend is unreachable' },
      { status: 502 }
    );
  }
}
