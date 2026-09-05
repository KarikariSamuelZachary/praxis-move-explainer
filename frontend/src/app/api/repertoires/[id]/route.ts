import { NextRequest, NextResponse } from 'next/server';
import { auth } from '@clerk/nextjs/server';

/**
 * /api/repertoires/{id} - proxy to the FastAPI backend.
 *
 * Same auth + forwarding shape as src/app/api/repertoires/route.ts
 * (see that file for the rationale). Exposes GET (single-repertoire
 * fetch for the detail page's header - name/color - so a direct link
 * or a cold refresh doesn't depend on the LIST endpoint's cached
 * data) and DELETE (list-page removal). PUT/PATCH aren't part of this
 * surface yet.
 *
 * Next.js 16 note: dynamic route segments receive `params` as a
 * Promise that must be awaited (see
 * node_modules/next/dist/docs/01-app/01-getting-started/05-server-and-client-components.md).
 */

const BACKEND_API_URL = process.env.BACKEND_API_URL ?? 'http://localhost:8000';
const INTERNAL_SECRET = process.env.INTERNAL_SECRET ?? '';

type RouteContext = { params: Promise<{ id: string }> };

export async function GET(_request: NextRequest, context: RouteContext) {
  const { id } = await context.params;
  const backendUrl = new URL(`/api/repertoires/${encodeURIComponent(id)}`, BACKEND_API_URL);

  try {
    const { userId } = await auth();
    if (!userId) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    const response = await fetch(backendUrl, {
      headers: {
        Accept: 'application/json',
        'X-Internal-Secret': INTERNAL_SECRET,
        'X-Clerk-User-Id': userId,
      },
      cache: 'no-store',
    });

    return new NextResponse(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: {
        'content-type': response.headers.get('content-type') ?? 'application/json',
      },
    });
  } catch (error) {
    console.error('Repertoire get proxy error:', error);
    return NextResponse.json(
      { error: 'Backend is unreachable' },
      { status: 502 }
    );
  }
}

export async function DELETE(_request: NextRequest, context: RouteContext) {
  const { id } = await context.params;
  const backendUrl = new URL(`/api/repertoires/${encodeURIComponent(id)}`, BACKEND_API_URL);

  try {
    const { userId } = await auth();
    if (!userId) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    const response = await fetch(backendUrl, {
      method: 'DELETE',
      headers: {
        Accept: 'application/json',
        'X-Internal-Secret': INTERNAL_SECRET,
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
    console.error('Repertoire delete proxy error:', error);
    return NextResponse.json(
      { error: 'Backend is unreachable' },
      { status: 502 }
    );
  }
}
