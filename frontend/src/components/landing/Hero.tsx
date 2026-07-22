'use client';

import { useEffect, useLayoutEffect, useRef } from 'react';
import gsap from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';

import DustCanvas from './DustCanvas';

gsap.registerPlugin(ScrollTrigger);

type HeroProps = {
  onStartTraining: () => void;
  onExplore: () => void;
};

export default function Hero({ onStartTraining, onExplore }: HeroProps) {
  const sectionRef = useRef<HTMLElement>(null);
  const textRef = useRef<HTMLDivElement>(null);
  const reliefRef = useRef<HTMLDivElement>(null);
  const cutoutRef = useRef<HTMLDivElement>(null);
  const shadowRef = useRef<HTMLDivElement>(null);
  const hintRef = useRef<HTMLDivElement>(null);
  const spotlightRef = useRef<HTMLDivElement>(null);

  // Mouse-driven spotlight — polished wood catching light
  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    if (window.matchMedia('(hover: none)').matches) return;

    const section = sectionRef.current;
    const spotlight = spotlightRef.current;
    if (!section || !spotlight) return;

    const quickX = gsap.quickTo(spotlight, 'x', { duration: 0.9, ease: 'power3.out' });
    const quickY = gsap.quickTo(spotlight, 'y', { duration: 0.9, ease: 'power3.out' });

    // Rest position: soft light over the knight, upper right
    const rect = section.getBoundingClientRect();
    gsap.set(spotlight, { x: rect.width * 0.55, y: -rect.height * 0.1 });

    const onMove = (event: PointerEvent) => {
      const rect = section.getBoundingClientRect();
      quickX(event.clientX - rect.left - rect.width * 0.35);
      quickY(event.clientY - rect.top - rect.height * 0.35);
    };

    section.addEventListener('pointermove', onMove);
    return () => section.removeEventListener('pointermove', onMove);
  }, []);

  // Intro + scroll-driven "knight lifts out of the wood"
  useLayoutEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

    const ctx = gsap.context(() => {
      const intro = gsap.timeline({ defaults: { ease: 'power3.out' } });
      intro
        .fromTo(
          '[data-hero-line]',
          { y: 54, opacity: 0 },
          { y: 0, opacity: 1, duration: 1.4, stagger: 0.14, delay: 0.55 }
        )
        .fromTo(
          '[data-hero-sub]',
          { y: 30, opacity: 0 },
          { y: 0, opacity: 1, duration: 1.2 },
          '-=0.9'
        )
        .fromTo(
          '[data-hero-cta]',
          { y: 24, opacity: 0 },
          { y: 0, opacity: 1, duration: 1.1, stagger: 0.1 },
          '-=0.9'
        )
        .fromTo(
          reliefRef.current,
          { opacity: 0, scale: 1.05 },
          { opacity: 1, scale: 1, duration: 2.2, ease: 'power2.out' },
          0.3
        )
        .fromTo(
          hintRef.current,
          { opacity: 0 },
          { opacity: 1, duration: 1.4 },
          '-=0.8'
        );

      const mm = gsap.matchMedia();

      mm.add('(min-width: 1024px)', () => {
        const lift = gsap.timeline({
          scrollTrigger: {
            trigger: sectionRef.current,
            start: 'top top',
            end: '+=80%',
            scrub: 0.6,
            pin: true,
            anticipatePin: 1,
          },
        });

        lift
          .to(
            reliefRef.current,
            { scale: 1.14, opacity: 0, filter: 'blur(8px)', ease: 'none', duration: 0.6 },
            0
          )
          .fromTo(
            cutoutRef.current,
            { opacity: 0, y: 70, scale: 0.9 },
            { opacity: 1, y: -16, scale: 1.05, ease: 'none', duration: 0.75 },
            0.1
          )
          .to(
            shadowRef.current,
            { scaleX: 1.35, opacity: 0.25, ease: 'none', duration: 0.75 },
            0.1
          )
          .to(textRef.current, { y: -60, opacity: 0.2, ease: 'none', duration: 0.55 }, 0)
          .to(hintRef.current, { opacity: 0, duration: 0.12 }, 0);
      });

      mm.add('(max-width: 1023px)', () => {
        gsap.to(reliefRef.current, {
          scale: 1.08,
          opacity: 0.35,
          ease: 'none',
          scrollTrigger: {
            trigger: sectionRef.current,
            start: 'top top',
            end: 'bottom top',
            scrub: 0.6,
          },
        });
      });
    }, sectionRef);

    return () => ctx.revert();
  }, []);

  return (
    <section
      ref={sectionRef}
      id="home"
      className="relative flex min-h-screen items-center overflow-hidden"
    >
      {/* Mouse spotlight */}
      <div
        ref={spotlightRef}
        aria-hidden
        className="pointer-events-none absolute left-0 top-0 z-[1] h-[70vmax] w-[70vmax] opacity-60 mix-blend-soft-light"
        style={{
          background:
            'radial-gradient(circle, rgba(255,222,160,0.55) 0%, rgba(255,222,160,0.12) 34%, transparent 68%)',
        }}
      />

      <DustCanvas />

      <div className="relative z-10 mx-auto grid w-full max-w-[1400px] items-center gap-12 px-5 pb-24 pt-32 sm:px-8 lg:grid-cols-2 lg:gap-6 lg:pb-16 lg:pt-20 xl:pl-28">
        <div ref={textRef} className="max-w-2xl">
          <h1 className="font-display text-[2.3rem] font-semibold leading-[1.14] tracking-wide text-gold-bright sm:text-5xl lg:text-[2.9rem] xl:text-[3.35rem]">
            <span data-hero-line className="block">
              Train Like Masters.
            </span>
            <span data-hero-line className="block">
              Think Deeper.
            </span>
            <span data-hero-line className="block">
              Remember Longer.
            </span>
          </h1>

          <p
            data-hero-sub
            className="mt-8 max-w-lg text-base leading-7 text-wood-mute sm:text-lg sm:leading-8"
          >
            Praxis is your all-in-one chess training workspace.
            <br />
            5.8 million puzzles. Powerful game review.
            <br />
            FSPS Woodpecker spaced repetition.
          </p>

          <div className="mt-10 flex flex-wrap items-center gap-5">
            <button
              type="button"
              data-hero-cta
              onClick={onStartTraining}
              className="group inline-flex items-center gap-3 rounded-md bg-moss px-8 py-4 text-sm font-semibold tracking-wide text-white shadow-[0_10px_36px_rgba(46,158,91,0.4)] transition duration-300 hover:bg-moss-bright hover:shadow-[0_10px_44px_rgba(55,190,126,0.5)]"
            >
              Start Training
              <span className="transition-transform duration-300 group-hover:translate-x-1">
                →
              </span>
            </button>
            <button
              type="button"
              data-hero-cta
              onClick={onExplore}
              className="group inline-flex items-center gap-3 rounded-md px-2 py-4 text-sm font-medium tracking-wide text-cream/90 transition hover:text-gold-bright"
            >
              <span className="flex h-9 w-9 items-center justify-center rounded-full border border-cream/30 text-xs transition group-hover:border-gold/60">
                ▶
              </span>
              Explore Praxis
            </button>
          </div>
        </div>

        {/* Knight visual: relief carved in wood, dimensional knight lifts out on scroll */}
        <div className="relative mx-auto flex h-[46vh] w-full max-w-md items-center justify-center lg:h-[78vh] lg:max-w-none">
          <div
            ref={reliefRef}
            className="absolute inset-0 flex items-center justify-center"
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src="/knight-relief.webp"
              alt="Chess knight carved into walnut"
              className="max-h-full w-auto rounded-full object-contain [mask-image:radial-gradient(ellipse_60%_56%_at_50%_47%,black_50%,transparent_74%)]"
            />
          </div>

          <div
            ref={cutoutRef}
            className="absolute inset-0 flex items-center justify-center opacity-0"
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src="/knight-cutout.webp"
              alt="Hand-carved wooden chess knight"
              className="max-h-full w-auto object-contain [filter:drop-shadow(0_50px_46px_rgba(0,0,0,0.55))]"
            />
          </div>

          <div
            ref={shadowRef}
            aria-hidden
            className="absolute bottom-[6%] left-1/2 h-[7%] w-[46%] -translate-x-1/2 rounded-[50%] bg-black/70 blur-2xl"
          />
        </div>
      </div>

      <div
        ref={hintRef}
        className="absolute bottom-8 left-1/2 z-10 flex -translate-x-1/2 flex-col items-center gap-3"
      >
        <div className="flex h-12 w-7 items-start justify-center rounded-full border border-cream/30 p-2">
          <div className="h-2 w-1 rounded-full bg-cream/70 [animation:scroll-dot_1.8s_ease-in-out_infinite]" />
        </div>
        <span className="text-[11px] uppercase tracking-[0.28em] text-wood-mute">
          Scroll to explore
        </span>
      </div>
    </section>
  );
}
