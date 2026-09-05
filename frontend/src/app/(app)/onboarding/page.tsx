'use client';

import { useRouter } from 'next/navigation';
import { useEffect, useState, type CSSProperties } from 'react';

import { KnightMark } from '@/components/layout/KnightMark';

type SkillLevel = 'new' | 'beginner' | 'intermediate' | 'advanced';
type WoodTone = 'dark' | 'light';

type Level = {
  value: SkillLevel;
  label: string;
  description: string;
  rating: string;
  tone: WoodTone;
};

// Cards alternate between the same two walnut textures the chess board
// uses for its dark/light squares (BoardShell.tsx:385-394) so the
// picker reads as part of the same furniture as the rest of the app.
const levels: Level[] = [
  {
    value: 'new',
    label: 'New to Chess',
    description: 'Learning how pieces coordinate and basic tactics.',
    rating: '800–1000',
    tone: 'light',
  },
  {
    value: 'beginner',
    label: 'Beginner',
    description: 'Comfortable spotting forks, pins, and simple combinations.',
    rating: '1000–1300',
    tone: 'dark',
  },
  {
    value: 'intermediate',
    label: 'Intermediate',
    description: 'Regularly finds multi-move tactics and calculation sequences.',
    rating: '1300–1600',
    tone: 'light',
  },
  {
    value: 'advanced',
    label: 'Advanced',
    description: 'Strong tactical vision and deeper calculation ability.',
    rating: '1600+',
    tone: 'dark',
  },
];

const DARK_WOOD =
  'linear-gradient(rgba(0,0,0,0.5),rgba(0,0,0,0.5)), url(/walnut-dark.webp)';
const LIGHT_WOOD =
  'linear-gradient(rgba(0,0,0,0.18),rgba(0,0,0,0.18)), url(/walnut-light.webp)';

// Label/description/rating colors per tone. Mirrors the chess board's
// notation contrast: dark squares carry cream/gold labels, light
// squares carry deep-walnut labels (ChessBoard.tsx:664-669).
const TEXT: Record<WoodTone, { label: string; description: string; rating: string }> = {
  dark: {
    label: '#efd9a7',
    description: 'rgba(237, 227, 208, 0.82)',
    rating: 'rgba(237, 227, 208, 0.58)',
  },
  light: {
    label: '#3a2410',
    description: 'rgba(26, 10, 2, 0.78)',
    rating: 'rgba(26, 10, 2, 0.6)',
  },
};

function cardStyle(tone: WoodTone, selected: boolean): CSSProperties {
  const backgroundImage = tone === 'dark' ? DARK_WOOD : LIGHT_WOOD;
  // Selected → emerald ring + soft emerald glow on top of the wood
  // grain (keeps the existing accent while letting the texture show).
  const boxShadow = selected
    ? '0 0 0 2px #10b981, 0 10px 30px rgba(16,185,129,0.25), inset 0 1px 0 rgba(255,255,255,0.06), inset 0 -1px 0 rgba(0,0,0,0.5)'
    : '0 0 0 1px rgba(0,0,0,0.5), 0 10px 30px rgba(0,0,0,0.55), inset 0 1px 0 rgba(255,255,255,0.06), inset 0 -1px 0 rgba(0,0,0,0.5)';
  return {
    backgroundImage,
    backgroundSize: 'cover',
    backgroundPosition: 'center',
    boxShadow,
  };
}

export default function OnboardingPage() {
  const router = useRouter();
  const [selected, setSelected] = useState<SkillLevel | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  // The middleware (proxy.ts) already redirects users who have a skill
  // level away from /onboarding, so by the time we render here the
  // form is safe to show immediately. This background check is only a
  // safety net against a race (e.g. the lookup landed mid-redirect) -
  // it must never block the UI with a spinner.
  useEffect(() => {
    let cancelled = false;
    fetch('/api/onboarding/skill-level', { cache: 'no-store' })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (!cancelled && data?.skill_level) {
          router.replace('/puzzles');
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [router]);

  async function handleSubmit() {
    if (!selected || submitting) return;
    setError('');
    setSubmitting(true);
    try {
      const res = await fetch('/api/onboarding/skill-level', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ skill_level: selected }),
      });
      if (res.ok) {
        router.push('/puzzles');
        router.refresh();
        return;
      }
      let message = 'Something went wrong. Please try again.';
      try {
        const data = await res.json();
        if (data?.error) message = data.error;
      } catch {}
      setError(message);
    } catch {
      setError('Network error. Please try again.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="h-[calc(100vh-3rem)] overflow-y-auto text-white [background-image:url(/walnut-dark.webp)] [background-size:cover] [background-position:center]">
      <div className="flex min-h-full flex-col items-center justify-center px-4 py-8">
        {/* Praxis logo - the same KnightMark + gold wordmark the app nav
            and landing page use (TopNav.tsx:77-78, LandingNav.tsx:62-65). */}
        <div className="mb-6 flex items-center gap-3">
          <KnightMark className="h-10 w-8" />
          <span className="font-display text-2xl font-bold tracking-[0.16em] text-gold">
            PRAXIS
          </span>
        </div>

        <h1 className="text-2xl font-semibold text-gold-bright mb-6 text-center">
          What&apos;s your chess level?
        </h1>

        <div className="grid grid-cols-2 gap-4 w-full max-w-lg mb-6">
          {levels.map((level) => {
            const isSelected = selected === level.value;
            const colors = TEXT[level.tone];
            return (
              <button
                key={level.value}
                onClick={() => setSelected(level.value)}
                style={cardStyle(level.tone, isSelected)}
                className="text-left rounded-2xl p-4 cursor-pointer transition-transform duration-200 hover:-translate-y-0.5"
              >
                <div
                  className="font-display text-base font-semibold mb-1"
                  style={{ color: colors.label }}
                >
                  {level.label}
                </div>
                <div className="text-sm mb-2" style={{ color: colors.description }}>
                  {level.description}
                </div>
                <div className="text-xs font-medium" style={{ color: colors.rating }}>
                  {level.rating}
                </div>
              </button>
            );
          })}
        </div>

        {error && (
          <p className="mb-4 max-w-md rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-4 py-2.5 text-center text-sm text-emerald-100">
            {error}
          </p>
        )}

        <button
          onClick={handleSubmit}
          disabled={!selected || submitting}
          className={`px-8 py-2.5 rounded-lg font-medium text-sm transition-colors ${
            selected && !submitting
              ? 'bg-emerald-500 text-zinc-950 hover:bg-emerald-400 cursor-pointer'
              : 'bg-zinc-800 text-zinc-500 cursor-not-allowed'
          }`}
        >
          {submitting ? 'Saving…' : 'Start Training'}
        </button>
      </div>
    </div>
  );
}
