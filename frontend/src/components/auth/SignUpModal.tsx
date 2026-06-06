'use client';

import { FormEvent, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '@clerk/nextjs';
import { useSignUp } from '@clerk/react/legacy';

import Modal from './Modal';

type SignUpModalProps = {
  onClose: () => void;
  onSwitchToSignIn: () => void;
};

function PraxisLogo() {
  return (
    <div className="flex items-center justify-center gap-3 text-emerald-400">
      <div className="relative h-9 w-9 rounded-lg border border-emerald-400/40 bg-emerald-500/10">
        <div className="absolute left-2 top-1.5 h-6 w-2 rounded-full bg-emerald-400" />
        <div className="absolute left-3 top-1.5 h-3 w-5 rounded-r-full bg-emerald-300" />
        <div className="absolute left-3 top-[18px] h-2 w-5 rounded-r-full bg-emerald-500" />
      </div>
      <span className="text-2xl font-semibold uppercase tracking-[0.22em]">
        Praxis
      </span>
    </div>
  );
}

function UserIcon() {
  return (
    <svg aria-hidden="true" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.6" viewBox="0 0 24 24">
      <path d="M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8z" />
      <path d="M4 21a8 8 0 0 1 16 0" />
    </svg>
  );
}

function MailIcon() {
  return (
    <svg aria-hidden="true" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.6" viewBox="0 0 24 24">
      <path d="M4 6h16v12H4z" />
      <path d="m4 7 8 6 8-6" />
    </svg>
  );
}

function LockIcon() {
  return (
    <svg aria-hidden="true" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.6" viewBox="0 0 24 24">
      <path d="M7 10V8a5 5 0 0 1 10 0v2" />
      <path d="M6 10h12v10H6z" />
    </svg>
  );
}

function GoogleMark() {
  return (
    <span className="flex h-6 w-6 items-center justify-center rounded-full bg-zinc-50 text-sm font-black text-emerald-500">
      G
    </span>
  );
}

function fieldError(error: unknown): string {
  if (typeof error === 'object' && error && 'errors' in error) {
    const errors = (error as { errors?: { longMessage?: string; message?: string }[] }).errors;
    return errors?.[0]?.longMessage ?? errors?.[0]?.message ?? 'Something went wrong.';
  }

  return 'Something went wrong.';
}

export default function SignUpModal({ onClose, onSwitchToSignIn }: SignUpModalProps) {
  const router = useRouter();
  const { isSignedIn } = useAuth();
  const { isLoaded, signUp, setActive } = useSignUp();
  const [username, setUsername] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [needsVerification, setNeedsVerification] = useState(false);
  const [error, setError] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    if (isSignedIn) {
      router.push('/puzzles');
    }
  }, [isSignedIn, router]);

  if (isSignedIn) {
    return null;
  }

  async function finishSignup(sessionId: string) {
    if (!setActive) return;
    await setActive({ session: sessionId });
    router.push('/puzzles');
    router.refresh();
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!isLoaded) return;

    setError('');
    setIsSubmitting(true);

    try {
      const result = await signUp.create({
        username,
        emailAddress: email,
        password,
      });

      if (result.status === 'complete' && result.createdSessionId) {
        await finishSignup(result.createdSessionId);
        return;
      }

      await signUp.prepareEmailAddressVerification({ strategy: 'email_code' });
      setNeedsVerification(true);
    } catch (caughtError) {
      setError(fieldError(caughtError));
    } finally {
      setIsSubmitting(false);
    }
  }

  async function handleVerification(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!isLoaded) return;

    setError('');
    setIsSubmitting(true);

    try {
      const result = await signUp.attemptEmailAddressVerification({ code });

      if (result.status === 'complete' && result.createdSessionId) {
        await finishSignup(result.createdSessionId);
        return;
      }

      setError('Verification is not complete yet.');
    } catch (caughtError) {
      setError(fieldError(caughtError));
    } finally {
      setIsSubmitting(false);
    }
  }

  async function handleGoogle() {
    if (!isLoaded) return;

    await signUp.authenticateWithRedirect({
      strategy: 'oauth_google',
      redirectUrl: '/',
      redirectUrlComplete: '/puzzles',
    });
  }

  return (
    <Modal onClose={onClose}>
      <div className="mx-auto flex w-full max-w-[360px] flex-col">
        <PraxisLogo />
        <div className="mt-8 text-center">
          <h2 className="text-2xl font-bold tracking-tight text-zinc-50">
            {needsVerification ? 'Verify your email' : 'Create your account'}
          </h2>
          <p className="mt-3 text-sm text-zinc-300">
            {needsVerification
              ? 'Enter the code we sent to your email.'
              : 'Start your journey to chess improvement.'}
          </p>
        </div>

        {needsVerification ? (
          <form className="mt-8 space-y-5" onSubmit={handleVerification}>
            <label className="block">
              <span className="text-sm font-medium text-zinc-50">Verification code</span>
              <span className="mt-2 flex h-12 items-center gap-3 rounded-md border border-zinc-600 bg-zinc-900/40 px-4 text-zinc-400 transition focus-within:border-emerald-400">
                <MailIcon />
                <input
                  type="text"
                  value={code}
                  onChange={(event) => setCode(event.target.value)}
                  placeholder="Enter your code"
                  required
                  className="h-full min-w-0 flex-1 bg-transparent text-sm text-zinc-100 outline-none placeholder:text-zinc-500"
                />
              </span>
            </label>

            {error && (
              <p className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-100">
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={!isLoaded || isSubmitting}
              className="flex h-[52px] w-full items-center justify-center rounded-md bg-emerald-500 px-5 text-sm font-bold uppercase tracking-[0.08em] text-zinc-950 shadow-lg shadow-emerald-500/20 transition hover:bg-emerald-400 disabled:cursor-not-allowed disabled:bg-zinc-600 disabled:text-zinc-300"
            >
              {isSubmitting ? 'Verifying...' : 'Verify Email'}
            </button>
          </form>
        ) : (
          <>
            <form className="mt-8 space-y-5" onSubmit={handleSubmit}>
              <label className="block">
                <span className="text-sm font-medium text-zinc-50">Username</span>
                <span className="mt-2 flex h-12 items-center gap-3 rounded-md border border-zinc-600 bg-zinc-900/40 px-4 text-zinc-400 transition focus-within:border-emerald-400">
                  <UserIcon />
                  <input
                    type="text"
                    value={username}
                    onChange={(event) => setUsername(event.target.value)}
                    placeholder="Choose a username"
                    required
                    className="h-full min-w-0 flex-1 bg-transparent text-sm text-zinc-100 outline-none placeholder:text-zinc-500"
                  />
                </span>
              </label>

              <label className="block">
                <span className="text-sm font-medium text-zinc-50">Email</span>
                <span className="mt-2 flex h-12 items-center gap-3 rounded-md border border-zinc-600 bg-zinc-900/40 px-4 text-zinc-400 transition focus-within:border-emerald-400">
                  <MailIcon />
                  <input
                    type="email"
                    value={email}
                    onChange={(event) => setEmail(event.target.value)}
                    placeholder="you@example.com"
                    required
                    className="h-full min-w-0 flex-1 bg-transparent text-sm text-zinc-100 outline-none placeholder:text-zinc-500"
                  />
                </span>
              </label>

              <label className="block">
                <span className="text-sm font-medium text-zinc-50">Password</span>
                <span className="mt-2 flex h-12 items-center gap-3 rounded-md border border-zinc-600 bg-zinc-900/40 px-4 text-zinc-400 transition focus-within:border-emerald-400">
                  <LockIcon />
                  <input
                    type="password"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    placeholder="Create a strong password"
                    required
                    className="h-full min-w-0 flex-1 bg-transparent text-sm text-zinc-100 outline-none placeholder:text-zinc-500"
                  />
                </span>
              </label>

              {error && (
                <p className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-100">
                  {error}
                </p>
              )}

              <button
                type="submit"
                disabled={!isLoaded || isSubmitting}
                className="flex h-[52px] w-full items-center justify-center rounded-md bg-emerald-500 px-5 text-sm font-bold uppercase tracking-[0.08em] text-zinc-950 shadow-lg shadow-emerald-500/20 transition hover:bg-emerald-400 disabled:cursor-not-allowed disabled:bg-zinc-600 disabled:text-zinc-300"
              >
                {isSubmitting ? 'Creating...' : 'Sign Up'}
              </button>
            </form>

            <div className="my-7 flex items-center gap-4 text-sm text-zinc-400">
              <span className="h-px flex-1 bg-zinc-700" />
              <span>or</span>
              <span className="h-px flex-1 bg-zinc-700" />
            </div>

            <button
              type="button"
              onClick={handleGoogle}
              disabled={!isLoaded}
              className="flex h-12 w-full items-center justify-center gap-3 rounded-md border border-zinc-600 bg-zinc-800 text-sm font-semibold text-zinc-50 transition hover:border-emerald-400 disabled:cursor-not-allowed disabled:opacity-70"
            >
              <GoogleMark />
              Continue with Google
            </button>
          </>
        )}

        <p className="mt-8 text-center text-sm text-zinc-100">
          Already have an account?{' '}
          <button
            type="button"
            onClick={onSwitchToSignIn}
            className="font-medium text-emerald-300 transition hover:text-emerald-200"
          >
            Sign in
          </button>
        </p>
      </div>
    </Modal>
  );
}
