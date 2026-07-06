'use client';

import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';

type SkillLevel = 'new' | 'beginner' | 'intermediate' | 'advanced';

const levels: {
  value: SkillLevel;
  label: string;
  description: string;
  rating: string;
}[] = [
  {
    value: 'new',
    label: 'New to Chess',
    description: 'Learning how pieces coordinate and basic tactics.',
    rating: '800–1000',
  },
  {
    value: 'beginner',
    label: 'Beginner',
    description: 'Comfortable spotting forks, pins, and simple combinations.',
    rating: '1000–1300',
  },
  {
    value: 'intermediate',
    label: 'Intermediate',
    description: 'Regularly finds multi-move tactics and calculation sequences.',
    rating: '1300–1600',
  },
  {
    value: 'advanced',
    label: 'Advanced',
    description: 'Strong tactical vision and deeper calculation ability.',
    rating: '1600+',
  },
];

export default function OnboardingPage() {
  const router = useRouter();
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<SkillLevel | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    fetch('/api/onboarding/skill-level')
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data?.skill_level) {
          router.push('/puzzles');
        } else {
          setLoading(false);
        }
      })
      .catch(() => setLoading(false));
  }, [router]);

  async function handleSubmit() {
    if (!selected || submitting) return;
    setSubmitting(true);
    try {
      const res = await fetch('/api/onboarding/skill-level', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ skill_level: selected }),
      });
      if (res.ok) {
        setSubmitting(false);
        router.push('/puzzles');
        router.refresh();
      } else {
        setSubmitting(false);
      }
    } catch {
      setSubmitting(false);
    }
  }

  if (loading) {
    return (
      <div className="flex min-h-[calc(100vh-4rem)] items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-zinc-700 border-t-emerald-500" />
      </div>
    );
  }

  return (
    <div className="flex min-h-[calc(100vh-4rem)] flex-col items-center justify-center px-4">
      <img src="/logo.svg" alt="Praxis" className="h-10 w-auto mb-8" />

      <h1 className="text-2xl font-semibold text-zinc-100 mb-2">
        What&apos;s your chess level?
      </h1>
      <p className="text-sm text-zinc-400 mb-8 text-center max-w-md">
        We&apos;ll use this to find puzzles that match your current strength.
      </p>

      <div className="grid grid-cols-2 gap-4 w-full max-w-lg mb-8">
        {levels.map((level) => {
          const isSelected = selected === level.value;
          return (
            <button
              key={level.value}
              onClick={() => setSelected(level.value)}
              className={`text-left rounded-lg border p-5 transition-colors cursor-pointer ${
                isSelected
                  ? 'border-emerald-500 bg-emerald-500/10'
                  : 'border-zinc-700 bg-zinc-800 hover:border-zinc-600'
              }`}
            >
              <div className={`font-medium mb-1 ${isSelected ? 'text-emerald-400' : 'text-zinc-100'}`}>
                {level.label}
              </div>
              <div className="text-sm text-zinc-400 mb-2">{level.description}</div>
              <div className="text-xs text-zinc-500">{level.rating}</div>
            </button>
          );
        })}
      </div>

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
  );
}
