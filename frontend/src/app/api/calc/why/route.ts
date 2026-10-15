import { NextResponse } from 'next/server';
import { auth } from '@clerk/nextjs/server';
import { getBackendConfig } from '@/lib/backend';

// Proxy for the Blind Calculation "show why" endpoint. The client sends
// only the attempt id from the submit response (plus kind, default solve);
// the backend derives everything server-side and never reveals more of the
// correct line than the failure already did.
//
// Longer timeout than the other calc routes (40 s): a cold engine start
// plus a 500k-node search. An expired timer surfaces as a clean 504 the
// page turns into a manual retry.
export async function POST(request: Request) {
  const { backendApiUrl, internalSecret } = getBackendConfig();
  const backendUrl = new URL('/api/calc/why', backendApiUrl);

  try {
    const { userId } = await auth();
    if (!userId) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    const body = await request.json().catch(() => null);
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 40000);
    try {
      const response = await fetch(backendUrl, {
        method: 'POST',
        cache: 'no-store',
        signal: controller.signal,
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
    } finally {
      clearTimeout(timer);
    }
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      return NextResponse.json(
        { error: 'The explanation took too long' },
        { status: 504 }
      );
    }
    console.error('Calc why proxy error:', error);
    return NextResponse.json(
      { error: 'Calculation backend is unreachable' },
      { status: 502 }
    );
  }
}
