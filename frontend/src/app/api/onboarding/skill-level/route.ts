import { NextRequest, NextResponse } from 'next/server';
import { auth, currentUser } from '@clerk/nextjs/server';
import { getBackendConfig } from '@/lib/backend';
import {
  SKILL_CACHE_COOKIE,
  SKILL_CACHE_MAX_AGE_SECONDS,
  serializeSkillCache,
} from '@/lib/skill-cache';

export async function GET(request: NextRequest) {
  try {
    const { userId } = await auth();
    if (!userId) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    const { backendApiUrl, internalSecret } = getBackendConfig();
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
    const { backendApiUrl, internalSecret } = getBackendConfig();
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

    const payload = await response.json().catch(() => null);
    const nextResponse = NextResponse.json(payload, {
      status: response.status,
      statusText: response.statusText,
    });

    // Prime the middleware's skill cache with what was just saved so the
    // immediate /puzzles navigation is not gated on another backend fetch.
    const skillLevel =
      typeof body?.skill_level === 'string' && body.skill_level ? body.skill_level : null;
    if (response.ok && skillLevel) {
      nextResponse.cookies.set(
        SKILL_CACHE_COOKIE,
        serializeSkillCache(userId, skillLevel),
        {
          httpOnly: true,
          sameSite: 'lax',
          secure: process.env.NODE_ENV === 'production',
          path: '/',
          maxAge: SKILL_CACHE_MAX_AGE_SECONDS,
        },
      );
    }

    return nextResponse;
  } catch (error) {
    console.error('Onboarding POST proxy error:', error);
    return NextResponse.json(
      { error: 'Backend unreachable' },
      { status: 502 },
    );
  }
}
