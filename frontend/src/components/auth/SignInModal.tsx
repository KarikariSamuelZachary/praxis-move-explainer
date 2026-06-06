'use client';

import { FormEvent, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '@clerk/nextjs';
import { useSignIn } from '@clerk/react/legacy';

import Modal from './Modal';

type SignInModalProps = {
  onClose: () => void;
  onSwitchToSignUp: () => void;
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

function EyeIcon({ hidden }: { hidden: boolean }) {
  return (
    <svg aria-hidden="true" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.6" viewBox="0 0 24 24">
      <path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6z" />
      <path d="M12 9.5a2.5 2.5 0 1 1 0 5 2.5 2.5 0 0 1 0-5z" />
      {hidden && <path d="M4 4l16 16" />}
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

export default function SignInModal({ onClose, onSwitchToSignUp }: SignInModalProps) {
  const router = useRouter();
  const { isSignedIn } = useAuth();
  const { isLoaded, signIn, setActive } = useSignIn();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
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

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!isLoaded) return;

    setError('');
    setIsSubmitting(true);

    try {
      const result = await signIn.create({
        identifier: email,
        password,
      });

      if (result.status === 'complete' && result.createdSessionId) {
        await setActive({ session: result.createdSessionId });
        router.push('/puzzles');
        router.refresh();
        return;
      }

      setError('This sign in needs another verification step.');
    } catch (caughtError) {
      setError(fieldError(caughtError));
    } finally {
      setIsSubmitting(false);
    }
  }

  async function handleGoogle() {
    if (!isLoaded) return;

    await signIn.authenticateWithRedirect({
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
            Welcome back
          </h2>
          <p className="mt-3 text-sm text-zinc-300">
            Sign in to continue your training.
          </p>
        </div>

        <form className="mt-8 space-y-5" onSubmit={handleSubmit}>
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
                type={showPassword ? 'text' : 'password'}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="Enter your password"
                required
                className="h-full min-w-0 flex-1 bg-transparent text-sm text-zinc-100 outline-none placeholder:text-zinc-500"
              />
              <button
                type="button"
                aria-label={showPassword ? 'Hide password' : 'Show password'}
                onClick={() => setShowPassword((current) => !current)}
                className="text-zinc-400 transition hover:text-emerald-300"
              >
                <EyeIcon hidden={!showPassword} />
              </button>
            </span>
          </label>

          <div className="flex justify-end">
            <button
              type="button"
              className="text-sm font-medium text-emerald-300 transition hover:text-emerald-200"
            >
              Forgot password?
            </button>
          </div>

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
            {isSubmitting ? 'Signing in...' : 'Sign In'}
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

        <p className="mt-8 text-center text-sm text-zinc-100">
          Don&apos;t have an account?{' '}
          <button
            type="button"
            onClick={onSwitchToSignUp}
            className="font-medium text-emerald-300 transition hover:text-emerald-200"
          >
            Sign up
          </button>
        </p>
      </div>
    </Modal>
  );
}
