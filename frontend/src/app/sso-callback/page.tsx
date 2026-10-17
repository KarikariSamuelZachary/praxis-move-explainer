'use client';

import { useEffect, useRef } from 'react';
import { AuthenticateWithRedirectCallback, useAuth } from '@clerk/nextjs';

import { KnightMark } from '@/components/layout/KnightMark';

export default function SSOCallbackPage() {
  const { isLoaded, isSignedIn } = useAuth();
  const startedAtRef = useRef<number | null>(null);
  const authCompletedRef = useRef(false);
  const redirectLoggedRef = useRef(false);

  useEffect(() => {
    const startedAt = Date.now();
    startedAtRef.current = startedAt;

    console.log('[TIMING] sso-callback start', {
      timestamp: new Date(startedAt).toISOString(),
    });

    const logRedirect = (event: string) => {
      if (redirectLoggedRef.current) return;
      redirectLoggedRef.current = true;
      console.log(`[TIMING] sso-callback ${event}`, {
        timestamp: new Date().toISOString(),
        elapsed_ms: Date.now() - startedAt,
        sign_in_redirect: '/puzzles',
        sign_up_redirect: '/onboarding',
      });
    };

    const handlePageHide = () => logRedirect('redirect/unload');
    window.addEventListener('pagehide', handlePageHide);

    return () => {
      window.removeEventListener('pagehide', handlePageHide);
      logRedirect('unmount/redirect');
    };
  }, []);

  useEffect(() => {
    const startedAt = startedAtRef.current;
    if (!isLoaded || !isSignedIn || startedAt === null || authCompletedRef.current) {
      return;
    }

    authCompletedRef.current = true;
    console.log('[TIMING] sso-callback auth complete', {
      timestamp: new Date().toISOString(),
      elapsed_ms: Date.now() - startedAt,
    });
  }, [isLoaded, isSignedIn]);

  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-8 [background-image:url(/walnut-dark.webp)] [background-size:cover] [background-position:center]">
      <div className="flex items-center gap-3">
        <KnightMark className="h-10 w-8" />
        <span className="font-display text-2xl font-bold tracking-[0.16em] text-gold">
          PRAXIS
        </span>
      </div>

      <div
        role="status"
        aria-live="polite"
        className="flex items-center gap-3 text-sm text-[#f7e5c6]/80"
      >
        <span
          aria-hidden="true"
          className="h-4 w-4 animate-spin rounded-full border-2 border-[#efd9a7]/30 border-t-[#efd9a7]"
        />
        Signing you in…
      </div>

      {/* Allow Google login attempts from new users to become sign-ups. */}
      <AuthenticateWithRedirectCallback
        transferable
        signInForceRedirectUrl="/puzzles"
        signUpForceRedirectUrl="/onboarding"
        signInFallbackRedirectUrl="/puzzles"
        signUpFallbackRedirectUrl="/onboarding"
      />
    </div>
  );
}

