'use client';

import { KnightMark } from './LandingNav';

type CtaSectionProps = {
  onSignUp: () => void;
};

export default function CtaSection({ onSignUp }: CtaSectionProps) {
  return (
    <section id="start" className="relative">
      <div className="relative overflow-hidden">
        {/* Walnut table backdrop */}
        <div
          aria-hidden
          className="absolute inset-0 bg-cover bg-center"
          style={{ backgroundImage: 'url(/cta-bg.webp)' }}
        />
        <div
          aria-hidden
          className="absolute inset-0"
          style={{
            background:
              'radial-gradient(ellipse 62% 88% at 50% 52%, rgba(10,6,4,0.82) 30%, rgba(10,6,4,0.35) 70%, rgba(18,12,8,0.9) 100%)',
          }}
        />

        <div className="relative mx-auto flex min-h-[80vh] w-full max-w-4xl flex-col items-center justify-center px-5 py-32 text-center">
          <div data-reveal>
            <KnightMark className="mx-auto h-20 w-16 [animation:float-slow_5s_ease-in-out_infinite]" />
          </div>

          <h2
            data-reveal
            data-reveal-delay="0.1"
            className="mt-8 font-display text-4xl font-semibold tracking-wide text-gold-bright sm:text-5xl lg:text-6xl"
          >
            Ready to elevate
            <br />
            your game?
          </h2>

          <p
            data-reveal
            data-reveal-delay="0.2"
            className="mt-6 max-w-md text-base leading-7 text-wood-mute sm:text-lg"
          >
            Join thousands of players training with purpose every day.
          </p>

          <div data-reveal data-reveal-delay="0.3">
            <button
              type="button"
              onClick={onSignUp}
              className="group mt-10 inline-flex items-center gap-3 rounded-md bg-moss px-10 py-4.5 text-base font-semibold tracking-wide text-white shadow-[0_12px_44px_rgba(46,158,91,0.45)] transition duration-300 hover:bg-moss-bright hover:shadow-[0_12px_52px_rgba(55,190,126,0.55)]"
            >
              Create Free Account
              <span className="transition-transform duration-300 group-hover:translate-x-1">
                →
              </span>
            </button>
            <p className="mt-5 text-xs tracking-wide text-wood-mute/80">
              No credit card required.
            </p>
          </div>
        </div>
      </div>

      <footer className="relative border-t border-white/5">
        <div className="mx-auto flex w-full max-w-[1400px] items-center justify-between px-5 py-8 sm:px-8 xl:pl-28">
          <div className="flex items-center gap-3">
            <KnightMark className="h-6 w-5 opacity-80" />
            <span className="font-display text-xs font-semibold tracking-[0.28em] text-gold/80">
              PRAXIS
            </span>
          </div>
          <p className="text-xs text-wood-mute/70">
            © 2026 Praxis. Train with purpose.
          </p>
        </div>
      </footer>
    </section>
  );
}
