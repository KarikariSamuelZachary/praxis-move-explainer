import { AuthenticateWithRedirectCallback } from '@clerk/nextjs';

export default function SSOCallbackPage() {
  return (
    <div className="flex min-h-screen items-center justify-center [background-image:url(/walnut-dark.webp)] [background-size:cover] [background-position:center]">
      <AuthenticateWithRedirectCallback
        signInForceRedirectUrl="/puzzles"
        signUpForceRedirectUrl="/onboarding"
        signInFallbackRedirectUrl="/puzzles"
        signUpFallbackRedirectUrl="/onboarding"
      />
    </div>
  );
}
