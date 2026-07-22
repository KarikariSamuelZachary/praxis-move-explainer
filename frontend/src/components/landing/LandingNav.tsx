'use client';

import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import gsap from 'gsap';

export function KnightMark({ className = '' }: { className?: string }) {
  return (
    <div
      aria-hidden
      className={className}
      style={{
        WebkitMaskImage: 'url(/knight-mask.png)',
        maskImage: 'url(/knight-mask.png)',
        WebkitMaskSize: 'contain',
        maskSize: 'contain',
        WebkitMaskRepeat: 'no-repeat',
        maskRepeat: 'no-repeat',
        WebkitMaskPosition: 'center',
        maskPosition: 'center',
        background: 'linear-gradient(160deg, #efd9a7 10%, #d9b87c 55%, #a37f45 100%)',
      }}
    />
  );
}

const LINKS = [
  { label: 'Puzzles', hash: '#puzzles' },
  { label: 'Game Review', hash: '#review' },
  { label: 'Woodpecker', hash: '#woodpecker' },
  { label: 'Lessons', hash: '#more' },
  { label: 'Openings', hash: '#more' },
  { label: 'Community', hash: '#more' },
];

type LandingNavProps = {
  onSignIn: () => void;
  onSignUp: () => void;
  onNavigate: (hash: string) => void;
};

export default function LandingNav({ onSignIn, onSignUp, onNavigate }: LandingNavProps) {
  const [scrolled, setScrolled] = useState(false);
  const navRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 40);
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  useLayoutEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const ctx = gsap.context(() => {
      gsap.fromTo(
        navRef.current,
        { y: -24, opacity: 0 },
        { y: 0, opacity: 1, duration: 1.4, ease: 'power3.out', delay: 0.35 }
      );
    });
    return () => ctx.revert();
  }, []);

  return (
    <header
      ref={navRef}
      className={`fixed inset-x-0 top-0 z-50 transition-colors duration-500 ${
        scrolled
          ? 'border-b border-white/5 bg-walnut-950/85 backdrop-blur-md'
          : 'border-b border-transparent bg-transparent'
      }`}
    >
      <div className="mx-auto flex h-[72px] w-full max-w-[1400px] items-center justify-between px-5 sm:px-8">
        <button
          type="button"
          onClick={() => onNavigate('#home')}
          className="group flex items-center gap-3"
          aria-label="Praxis home"
        >
          <KnightMark className="h-9 w-7 transition-transform duration-500 group-hover:scale-105" />
          <span className="font-display text-lg font-semibold tracking-[0.28em] text-gold">
            PRAXIS
          </span>
        </button>

        <nav className="hidden items-center gap-8 lg:flex">
          {LINKS.map((link) => (
            <button
              key={link.label}
              type="button"
              onClick={() => onNavigate(link.hash)}
              className="text-[13px] font-medium tracking-wide text-wood-mute transition-colors duration-300 hover:text-gold-bright"
            >
              {link.label}
            </button>
          ))}
        </nav>

        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={onSignIn}
            className="hidden rounded-md px-4 py-2 text-sm font-medium text-cream/90 transition hover:bg-white/5 hover:text-gold-bright sm:inline-flex"
          >
            Log in
          </button>
          <button
            type="button"
            onClick={onSignUp}
            className="rounded-md bg-moss px-5 py-2.5 text-sm font-semibold text-white shadow-[0_8px_24px_rgba(46,158,91,0.35)] transition duration-300 hover:bg-moss-bright hover:shadow-[0_8px_32px_rgba(55,190,126,0.45)]"
          >
            Get Started
          </button>
        </div>
      </div>
    </header>
  );
}
