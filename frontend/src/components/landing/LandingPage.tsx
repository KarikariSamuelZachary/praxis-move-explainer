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

    // The hero pin-spacer is measured before the webfonts swap in, which can
    // yield a wrong document height (and a stale Lenis scroll limit). Re-measure
    // once fonts and the window finish loading so the scrollable area settles
    // before the user scrolls - otherwise the footer can become unreachable.
    const syncLayout = () => {
      ScrollTrigger.refresh();
      lenisRef.current?.resize();
    };

    document.fonts?.ready?.then(syncLayout).catch(() => {});
    if (document.readyState === 'complete') {
      syncLayout();
    } else {
      window.addEventListener('load', syncLayout);
    }

    return () => {
      window.removeEventListener('load', syncLayout);
      ctx.revert();
      gsap.ticker.remove(raf);
      lenis.destroy();
      lenisRef.current = null;
    };
  }, []);

  // Deep-brown viewport scrollbar (matches the navbar) while the landing
  // page is mounted. App pages use inner scroll containers, so this only
  // visibly affects the marketing route.
  useEffect(() => {
    document.documentElement.classList.add('landing-scrollbar');
    return () => {
      document.documentElement.classList.remove('landing-scrollbar');
    };
  }, []);

  // While an auth modal is open, freeze the landing page so scrolling only
  // happens inside the modal. Lenis is paused so wheel events can't move the
  // page, and body overflow is locked as a fallback (touch, reduced motion).
  useEffect(() => {
    if (!authModal) return;

    lenisRef.current?.stop();
    const previousBodyOverflow = document.body.style.overflow;
    const previousHtmlOverflow = document.documentElement.style.overflow;
    document.body.style.overflow = 'hidden';
    document.documentElement.style.overflow = 'hidden';

    return () => {
      lenisRef.current?.start();
      document.body.style.overflow = previousBodyOverflow;
      document.documentElement.style.overflow = previousHtmlOverflow;
    };
  }, [authModal]);

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
      {/* Same walnut backdrop the app pages use - fixed, sections scroll over it */}
      <div
        aria-hidden
        className="pointer-events-none fixed inset-0 z-0 bg-cover bg-center"
        style={{ backgroundImage: 'url(/walnut-dark.webp)' }}
      />
      {/* Page-wide warm vignette - light enough to let the walnut grain show */}
      <div
        aria-hidden
        className="pointer-events-none fixed inset-0 z-0"
        style={{
          background:
            'radial-gradient(ellipse 90% 70% at 50% 30%, rgba(43,28,16,0.38), rgba(14,9,6,0.66) 80%)',
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
