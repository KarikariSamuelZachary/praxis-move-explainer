'use client';

import { useEffect, useRef, useState } from 'react';
import gsap from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';

import SectionHeading from './SectionHeading';

gsap.registerPlugin(ScrollTrigger);

const CYCLE_WORDS = ['Strengthen.', 'Repeat.', 'Remember.'];

type WoodpeckerSectionProps = {
  onStartReview: () => void;
};

export default function WoodpeckerSection({ onStartReview }: WoodpeckerSectionProps) {
  const cardRef = useRef<HTMLDivElement>(null);
  const birdRef = useRef<HTMLImageElement>(null);
  const countRef = useRef<HTMLSpanElement>(null);
  const checkRef = useRef<HTMLSpanElement>(null);
  const [wordIndex, setWordIndex] = useState(0);

  // Rotating caption words
  useEffect(() => {
    const id = setInterval(() => {
      setWordIndex((i) => (i + 1) % CYCLE_WORDS.length);
    }, 2200);
    return () => clearInterval(id);
  }, []);

  // Countdown + peck when the card scrolls into view
  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const card = cardRef.current;
    if (!card) return;

    const ctx = gsap.context(() => {
      const counter = { value: 12 };

      gsap.set(checkRef.current, { scale: 0, opacity: 0 });

      const tl = gsap.timeline({
        scrollTrigger: { trigger: card, start: 'top 70%', once: true },
      });

      // One gentle peck
      tl.to(birdRef.current, {
        rotate: -9,
        y: 4,
        duration: 0.16,
        ease: 'power2.in',
        transformOrigin: '72% 88%',
        delay: 0.7,
      }).to(birdRef.current, {
        rotate: 0,
        y: 0,
        duration: 0.5,
        ease: 'elastic.out(1, 0.45)',
      });

      // Reviews tick down to zero
      tl.to(
        counter,
        {
          value: 0,
          duration: 1.7,
          ease: 'power1.inOut',
          onUpdate: () => {
            if (countRef.current) {
              countRef.current.textContent = String(Math.round(counter.value));
            }
          },
        },
        0.9
      );

      // Gold check
      tl.to(
        checkRef.current,
        { scale: 1, opacity: 1, duration: 0.5, ease: 'back.out(2.2)' },
        2.8
      );
    }, card);

    return () => ctx.revert();
  }, []);

  return (
    <section id="woodpecker" className="relative">
      <div className="mx-auto grid w-full max-w-[1400px] items-center gap-14 px-5 py-28 sm:px-8 lg:grid-cols-2 lg:gap-20 lg:py-40 xl:pl-28">
        <SectionHeading
          label="Repeat. Remember. Master."
          title={
            <>
              FSPS Woodpecker
              <br />
              Spaced Repetition.
              <br />
              Built In.
            </>
          }
          copy={
            <>
              Reviews scheduled at the right time.
              <br />
              Forget less. Improve more.
              <br />
              Strengthen the positions that matter.
            </>
          }
          footnote="Smarter repetition. Bigger gains."
        />

        <div data-reveal data-reveal-delay="0.15">
          <div
            ref={cardRef}
            className="relative mx-auto max-w-xl overflow-hidden rounded-2xl border border-white/10 shadow-[0_30px_80px_rgba(0,0,0,0.55)]"
            style={{
              background:
                'linear-gradient(rgba(18,11,7,0.72), rgba(18,11,7,0.86)), url(/wood-panel-dark.png)',
              backgroundSize: 'cover',
              backgroundPosition: 'center',
            }}
          >
            <div className="flex items-center gap-6 p-8 sm:gap-10 sm:p-10">
              {/* Bird burned into the wood */}
              <div className="relative shrink-0">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  ref={birdRef}
                  src="/woodpecker-cutout.webp"
                  alt="Woodpecker carved into wood"
                  className="h-36 w-auto sm:h-44 [filter:brightness(0.82)_sepia(0.25)_drop-shadow(0_10px_18px_rgba(0,0,0,0.5))]"
                />
              </div>

              <div className="min-w-0 flex-1">
                <div className="text-xs font-semibold uppercase tracking-[0.25em] text-wood-mute">
                  Reviews Due
                </div>
                <div className="mt-2 flex items-baseline gap-3">
                  <span
                    ref={countRef}
                    className="font-display text-6xl font-semibold text-gold-bright"
                  >
                    12
                  </span>
                  <span
                    ref={checkRef}
                    className="font-display text-3xl font-bold text-moss-bright opacity-0"
                  >
                    ✓
                  </span>
                </div>
                <div className="mt-3 flex items-center gap-2 text-sm text-wood-mute">
                  <span aria-hidden>◷</span> 8 min
                  <span className="text-wood-mute/70">estimated time</span>
                </div>
                <button
                  type="button"
                  onClick={onStartReview}
                  className="group mt-6 inline-flex items-center gap-2 rounded-md bg-moss px-6 py-3 text-sm font-semibold text-white shadow-[0_8px_28px_rgba(46,158,91,0.35)] transition duration-300 hover:bg-moss-bright"
                >
                  Start Review
                  <span className="transition-transform duration-300 group-hover:translate-x-1">
                    →
                  </span>
                </button>
              </div>
            </div>
          </div>

          <p className="mt-8 text-center font-display text-xl tracking-wide text-gold/80">
            <span key={wordIndex} className="inline-block [animation:float-slow_2.2s_ease-in-out]">
              {CYCLE_WORDS[wordIndex]}
            </span>
          </p>
        </div>
      </div>
    </section>
  );
}
