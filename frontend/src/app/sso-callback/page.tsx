import { AuthenticateWithRedirectCallback } from '@clerk/nextjs';

export default function SSOCallbackPage() {
  return (
    <AuthenticateWithRedirectCallback
      signInForceRedirectUrl="/puzzles"
      signUpForceRedirectUrl="/puzzles"
      signInFallbackRedirectUrl="/puzzles"
      signUpFallbackRedirectUrl="/puzzles"
    />
  );
}
