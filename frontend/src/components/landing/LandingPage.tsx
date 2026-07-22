'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import gsap from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';
import Lenis from 'lenis';

import SignInModal from '../auth/SignInModal';
import SignUpModal from '../auth/SignUpModal';
import LandingNav from './LandingNav';
import RailNav from './RailNav';
import Hero from './Hero';
import PuzzlesSection from './PuzzlesSection';
import ReviewSection from './ReviewSection';
import WoodpeckerSection from './WoodpeckerSection';
import MoreSection from './MoreSection';
import CtaSection from './CtaSection';

export default function LandingPage() {
  const [authModal, setAuthModal] = useState<'sign-in' | 'sign-up' | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const lenisRef = useRef<Lenis | null>(null);

  useEffect(() => {
    gsap.registerPlugin(ScrollTrigger);

    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduced) {
      return;
    }

    const lenis = new Lenis({ duration: 1.15, smoothWheel: true });
    lenisRef.current = lenis;
    lenis.on('scroll', ScrollTrigger.update);

    const raf = (time: number) => {
      lenis.raf(time * 1000);
    };
    gsap.ticker.add(raf);
    gsap.ticker.lagSmoothing(0);

    const ctx = gsap.context(() => {
      gsap.utils.toArray<HTMLElement>('[data-reveal]').forEach((el) => {
        gsap.fromTo(
          el,
          { y: 44, opacity: 0 },
          {
            y: 0,
            opacity: 1,
            duration: 1.2,
            ease: 'power3.out',
            delay: Number(el.dataset.revealDelay ?? 0),
            scrollTrigger: { trigger: el, start: 'top 86%', once: true },
          }
        );
      });
    }, rootRef);

    return () => {
      ctx.revert();
      gsap.ticker.remove(raf);
      lenis.destroy();
      lenisRef.current = null;
    };
  }, []);

  const scrollToSection = useCallback((hash: string) => {
    const target = document.querySelector(hash);
    if (!target) return;

    if (lenisRef.current) {
      lenisRef.current.scrollTo(target as HTMLElement, { offset: -72, duration: 1.6 });
    } else {
      (target as HTMLElement).scrollIntoView({ behavior: 'smooth' });
    }
  }, []);

  const openSignIn = useCallback(() => setAuthModal('sign-in'), []);
  const openSignUp = useCallback(() => setAuthModal('sign-up'), []);

  return (
    <div
      ref={rootRef}
      className="landing-root relative min-h-screen overflow-x-clip font-sans text-cream"
    >
      {/* Page-wide warm vignette over the wood texture */}
      <div
        aria-hidden
        className="pointer-events-none fixed inset-0 z-0"
        style={{
          background:
            'radial-gradient(ellipse 90% 70% at 50% 30%, rgba(43,28,16,0.55), rgba(10,6,4,0.92) 78%)',
        }}
      />

      <LandingNav
        onSignIn={openSignIn}
        onSignUp={openSignUp}
        onNavigate={scrollToSection}
      />
      <RailNav onNavigate={scrollToSection} />

      <main className="relative z-10">
        <Hero
          onStartTraining={openSignUp}
          onExplore={() => scrollToSection('#puzzles')}
        />
        <PuzzlesSection />
        <ReviewSection />
        <WoodpeckerSection onStartReview={openSignUp} />
        <MoreSection />
        <CtaSection onSignUp={openSignUp} />
      </main>

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
