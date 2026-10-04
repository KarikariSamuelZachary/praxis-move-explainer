import { NextResponse } from 'next/server';
import { auth } from '@clerk/nextjs/server';

import { getBackendConfig } from '@/lib/backend';

export async function GET() {
  const { userId } = await auth();
  if (!userId) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }

  const { backendApiUrl, internalSecret } = getBackendConfig();
  const response = await fetch(new URL('/api/review/capabilities', backendApiUrl), {
    headers: {
      Accept: 'application/json',
      'X-Internal-Secret': internalSecret,
      'X-Clerk-User-Id': userId,
    },
  });

  return new NextResponse(await response.text(), {
    status: response.status,
    headers: {
      'content-type': response.headers.get('content-type') ?? 'application/json',
    },
  });
}
