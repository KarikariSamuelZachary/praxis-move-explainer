'use client';

import { useEffect, useState } from 'react';

const SECTIONS = [
  { num: '01', label: 'HOME', id: 'home' },
  { num: '02', label: 'PUZZLES', id: 'puzzles' },
  { num: '03', label: 'GAME REVIEW', id: 'review' },
  { num: '04', label: 'WOODPECKER', id: 'woodpecker' },
  { num: '05', label: 'MORE', id: 'more' },
  { num: '06', label: 'START', id: 'start' },
];

type RailNavProps = {
  onNavigate: (hash: string) => void;
};

export default function RailNav({ onNavigate }: RailNavProps) {
  const [active, setActive] = useState('home');

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setActive(entry.target.id);
          }
        }
      },
      { rootMargin: '-45% 0px -45% 0px' }
    );

    for (const section of SECTIONS) {
      const el = document.getElementById(section.id);
      if (el) observer.observe(el);
    }

    return () => observer.disconnect();
  }, []);

  return (
    <nav
      aria-label="Section navigation"
      className="fixed left-7 top-1/2 z-40 hidden -translate-y-1/2 xl:block"
    >
      <div className="relative flex flex-col gap-7">
        <div
          aria-hidden
          className="absolute -left-3 top-1 bottom-1 w-px bg-gradient-to-b from-transparent via-white/10 to-transparent"
        />
        {SECTIONS.map((section) => {
          const isActive = active === section.id;
          return (
            <button
              key={section.id}
              type="button"
              onClick={() => onNavigate(`#${section.id}`)}
              className="group flex flex-col items-start gap-1 text-left"
            >
              <span
                className={`font-display text-sm tracking-widest transition-colors duration-500 ${
                  isActive ? 'text-moss-bright' : 'text-stone-500 group-hover:text-gold'
                }`}
              >
                {section.num}
              </span>
              <span
                className={`text-[10px] font-semibold uppercase tracking-[0.22em] transition-colors duration-500 ${
                  isActive ? 'text-cream' : 'text-stone-600 group-hover:text-wood-mute'
                }`}
              >
                {section.label}
              </span>
            </button>
          );
        })}
      </div>
    </nav>
  );
}
