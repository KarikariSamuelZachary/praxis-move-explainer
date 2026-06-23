import { NextRequest, NextResponse } from 'next/server';
import { auth, currentUser } from '@clerk/nextjs/server';

export async function GET(request: NextRequest) {
  try {
    const { userId } = await auth();
    if (!userId) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    const backendApiUrl = process.env.BACKEND_API_URL ?? 'http://localhost:8000';
    const internalSecret = process.env.INTERNAL_SECRET ?? '';
    const backendUrl = new URL('/onboarding/skill-level', backendApiUrl);

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
    console.error('Onboarding GET proxy error:', error);
    return NextResponse.json(
      { error: 'Backend unreachable' },
      { status: 502 },
    );
  }
}

export async function POST(request: NextRequest) {
  try {
    const { userId } = await auth();
    if (!userId) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    const body = await request.json();
    const user = await currentUser();
    const email = user?.primaryEmailAddress?.emailAddress ?? '';
    const backendApiUrl = process.env.BACKEND_API_URL ?? 'http://localhost:8000';
    const internalSecret = process.env.INTERNAL_SECRET ?? '';
    const backendUrl = new URL('/onboarding/skill-level', backendApiUrl);

    const response = await fetch(backendUrl, {
      method: 'POST',
      cache: 'no-store',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        'X-Internal-Secret': internalSecret,
        'X-Clerk-User-Id': userId,
        'X-Clerk-User-Email': email,
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
    console.error('Onboarding POST proxy error:', error);
    return NextResponse.json(
      { error: 'Backend unreachable' },
      { status: 502 },
    );
  }
}
