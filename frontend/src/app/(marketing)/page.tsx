'use client';

import { useState } from 'react';
import Image from 'next/image';
import Link from 'next/link';

import SignInModal from '../../components/auth/SignInModal';
import SignUpModal from '../../components/auth/SignUpModal';

const features = [
  {
    icon: 'grid',
    title: 'Puzzles that teach',
    description:
      'Smart, adaptive tactics that focus on your weaknesses and turn missed ideas into lasting pattern recognition.',
  },
  {
    icon: 'search',
    title: 'Review with clarity',
    description:
      'Automatic game analysis with key moments, concise insights, and improvement suggestions you can act on.',
  },
  {
    icon: 'brain',
    title: 'AI coaching',
    description:
      'Get personalized recommendations and coaching based on your games, goals, and training rhythm.',
  },
  {
    icon: 'bars',
    title: 'Track your progress',
    description:
      'Visualize your rating trend, puzzle stats, review habits, and performance over time.',
  },
];

function PraxisMark() {
  return (
    <div className="flex items-center gap-3">
      <div className="relative h-8 w-8 rounded-lg border border-emerald-400/40 bg-emerald-500/10">
        <div className="absolute left-2 top-1.5 h-5 w-2 rounded-full bg-emerald-400" />
        <div className="absolute left-3 top-1.5 h-2.5 w-4 rounded-r-full bg-emerald-300" />
        <div className="absolute left-3 top-4 h-1.5 w-4 rounded-r-full bg-emerald-500" />
      </div>
      <span className="text-xl font-semibold uppercase tracking-[0.28em] text-emerald-300">
        Praxis
      </span>
    </div>
  );
}

function Icon({ name }: { name: string }) {
  if (name === 'search') {
    return (
      <div className="relative h-11 w-11">
        <div className="h-8 w-8 rounded-full border-4 border-emerald-400" />
        <div className="absolute bottom-1 right-1 h-4 w-1.5 rotate-[-45deg] rounded-full bg-emerald-400" />
      </div>
    );
  }

  if (name === 'brain') {
    return (
      <div className="grid h-11 w-11 grid-cols-2 gap-1.5">
        {Array.from({ length: 6 }).map((_, index) => (
          <span
            key={index}
            className="rounded-full border-2 border-emerald-400 bg-emerald-500/10"
          />
        ))}
      </div>
    );
  }

  if (name === 'bars') {
    return (
      <div className="flex h-11 w-11 items-end gap-1.5">
        {[18, 28, 38].map((height) => (
          <span
            key={height}
            style={{ height }}
            className="w-2.5 rounded-t-md border-2 border-emerald-400 bg-emerald-500/10"
          />
        ))}
      </div>
    );
  }

  return (
    <div className="grid h-11 w-11 grid-cols-2 gap-1.5">
      {Array.from({ length: 4 }).map((_, index) => (
        <span
          key={index}
          className="rounded-md border-2 border-emerald-400 bg-emerald-500/10"
        />
      ))}
    </div>
  );
}

export default function Home() {
  const [authModal, setAuthModal] = useState<'sign-in' | 'sign-up' | null>(null);

  return (
    <div className="min-h-screen overflow-hidden bg-zinc-950 text-zinc-100">
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_58%_42%,_rgba(16,185,129,0.24),_transparent_34%),radial-gradient(circle_at_20%_20%,_rgba(63,63,70,0.36),_transparent_30%)]" />
      <div className="absolute inset-x-0 top-0 h-px bg-zinc-700/60" />

      <div className="relative mx-auto flex min-h-screen w-full max-w-7xl flex-col px-5 py-6 sm:px-8 lg:px-10">
        <header className="flex items-center justify-between gap-6 border-b border-zinc-800/80 pb-6">
          <Link href="/" aria-label="Praxis home">
            <PraxisMark />
          </Link>

          <nav className="hidden items-center gap-9 text-xs font-semibold uppercase tracking-[0.12em] text-zinc-400 lg:flex">
            <Link className="transition hover:text-emerald-300" href="/puzzles">
              Puzzles
            </Link>
            <Link className="transition hover:text-emerald-300" href="/review">
              Review
            </Link>
          </nav>

          <div className="flex items-center gap-3 pr-28 md:pr-36 lg:pr-0">
            <button
              type="button"
              onClick={() => setAuthModal('sign-in')}
              className="hidden rounded-md px-4 py-2 text-sm font-semibold uppercase tracking-[0.08em] text-emerald-300 transition hover:bg-emerald-500/10 sm:inline-flex"
            >
              Log In
            </button>
            <button
              type="button"
              onClick={() => setAuthModal('sign-up')}
              className="rounded-md bg-emerald-400 px-5 py-3 text-sm font-bold uppercase tracking-[0.08em] text-zinc-950 shadow-lg shadow-emerald-500/20 transition hover:bg-emerald-300"
            >
              Sign Up
            </button>
          </div>
        </header>

        <main className="flex flex-1 flex-col justify-center gap-12 py-10 lg:py-12">
          <section className="grid items-center gap-10 lg:grid-cols-[0.95fr_1.25fr]">
            <div className="max-w-2xl">
              <div className="inline-flex items-center gap-3 rounded-full border border-emerald-500/20 bg-emerald-500/10 px-4 py-2 text-xs font-semibold uppercase tracking-[0.12em] text-emerald-300">
                <span className="h-2 w-2 rounded-full bg-emerald-300 shadow-[0_0_18px_rgba(110,231,183,0.8)]" />
                Purposeful training. Real results.
              </div>

              <h1 className="mt-7 max-w-xl text-5xl font-bold leading-[0.98] tracking-tight text-zinc-50 sm:text-6xl xl:text-7xl">
                Train. Understand.{' '}
                <span className="block text-emerald-300">Improve.</span>
              </h1>

              <p className="mt-6 max-w-xl text-lg leading-8 text-zinc-400">
                Praxis is your all-in-one chess training platform. Solve
                smarter puzzles, review with clarity, and get AI coaching
                tailored to your game.
              </p>

              <button
                type="button"
                onClick={() => setAuthModal('sign-up')}
                className="mt-8 inline-flex items-center justify-center rounded-lg bg-emerald-400 px-7 py-4 text-sm font-bold uppercase tracking-[0.08em] text-zinc-950 shadow-xl shadow-emerald-500/20 transition hover:bg-emerald-300"
              >
                Start Training
              </button>
            </div>

            <div className="relative min-h-[360px] overflow-hidden rounded-lg shadow-2xl shadow-black/40 lg:min-h-[520px]">
              <Image
                src="/hero-chesss.png"
                alt="Praxis chess training preview"
                fill
                priority
                sizes="(min-width: 1024px) 56vw, 100vw"
                className="object-cover"
              />
            </div>
          </section>

          <section className="grid gap-5 md:grid-cols-2 xl:grid-cols-4">
            {features.map((feature) => (
              <Link
                key={feature.title}
                href={feature.title.includes('Review') ? '/review' : '/puzzles'}
                className="group min-h-64 rounded-lg border border-zinc-800 bg-zinc-900/60 p-7 shadow-xl shadow-black/20 transition hover:border-emerald-500/40 hover:bg-zinc-900"
              >
                <Icon name={feature.icon} />
                <h2 className="mt-6 text-xl font-semibold tracking-tight text-zinc-50">
                  {feature.title}
                </h2>
                <p className="mt-4 text-base leading-7 text-zinc-400">
                  {feature.description}
                </p>
              </Link>
            ))}
          </section>
        </main>
      </div>
      {authModal === 'sign-in' && (
        <SignInModal
          onClose={() => setAuthModal(null)}
          onSwitchToSignUp={() => setAuthModal('sign-up')}
        />
      )}
      {authModal === 'sign-up' && (
        <SignUpModal
          onClose={() => setAuthModal(null)}
          onSwitchToSignIn={() => setAuthModal('sign-in')}
        />
      )}
    </div>
  );
}
