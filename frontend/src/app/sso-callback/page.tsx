import { AuthenticateWithRedirectCallback } from '@clerk/nextjs';

export default function SSOCallbackPage() {
  return (
    <AuthenticateWithRedirectCallback
      signInForceRedirectUrl="/puzzles"
      signUpForceRedirectUrl="/onboarding"
      signInFallbackRedirectUrl="/puzzles"
      signUpFallbackRedirectUrl="/onboarding"
    />
  );
}
