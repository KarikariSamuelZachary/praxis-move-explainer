'use client';

import Image from 'next/image';

import SectionHeading from './SectionHeading';
import DustCanvas from './DustCanvas';

type ModeCard = {
  title: string;
  description: string;
  illustration: string;
  cta: string;
};

const MODES: ModeCard[] = [
  {
    title: 'Opponent Preparation',
    description: 'Enter any Lichess or Chess.com username and face an AI version of their playing style.',
    illustration: '/opponent-prep-illustration.webp',
    cta: 'Start Opponent Prep',
  },
  {
    title: 'Engine Sparring',
    description: 'Challenge different versions of Stockfish with adjustable strength and playstyles.',
    illustration: '/engine-sparring-illustration.webp',
    cta: 'Choose Engine',
  },
  {
    title: 'Endgame Trainer',
    description: 'Master fundamental endgames through focused, position-based practice.',
    illustration: '/endgame-trainer-illustration.webp',
    cta: 'Explore Endgames',
  },
  {
    title: 'Scenario Trainer',
    description: 'Practice critical positions, converts, defenses, traps and more game situations.',
    illustration: '/scenario-trainer-illustration.webp',
    cta: 'Browse Scenarios',
  },
];

const PERSONA_TILES = [
  { name: 'Attacker', icon: '/persona-attacker.webp' },
  { name: 'Sacrificer', icon: '/persona-sacrificer.webp' },
  { name: 'Defender', icon: '/persona-defender.webp' },
  { name: 'Positional', icon: '/persona-positional.webp' },
  { name: 'Gambiter', icon: '/persona-gambiter.webp' },
];

function PersonaStrip() {
  return (
    <div className="mt-3 flex items-center gap-2">
      {PERSONA_TILES.map((persona) => (
        <div
          key={persona.name}
          className="flex h-8 w-8 items-center justify-center rounded-full bg-black/45 ring-1 ring-black/60"
        >
          <Image
            src={persona.icon}
            alt={persona.name}
            width={32}
            height={32}
            className="h-7 w-7 select-none object-contain"
          />
        </div>
      ))}
    </div>
  );
}

function ModeCardView({ mode, onCta }: { mode: ModeCard; onCta: () => void }) {
  return (
    <article className="group relative flex min-h-[16rem] flex-col overflow-hidden rounded-2xl border border-black/50 p-4 shadow-2xl shadow-black/30 transition duration-300 hover:border-[#d9b87c]/30 motion-safe:hover:-translate-y-1">
      <Image
        src={mode.illustration}
        alt=""
        fill
        sizes="(min-width: 1024px) 25vw, (min-width: 640px) 50vw, 100vw"
        className="select-none object-cover object-center opacity-30 transition-transform duration-500 motion-safe:group-hover:scale-[1.04]"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-gradient-to-b from-black/60 via-black/40 to-black/75"
      />

      <div className="relative">
        <h3 className="whitespace-pre-line font-display text-2xl font-semibold tracking-wide text-gold-bright">
          {mode.title}
        </h3>
        {mode.title === 'Engine Sparring' && <PersonaStrip />}
      </div>

      <p className="relative mt-4 max-w-xs text-sm leading-7 text-wood-mute">{mode.description}</p>

      <div className="relative flex-1" />

      <button
        type="button"
        onClick={onCta}
        className="relative mt-3 flex h-10 w-full cursor-pointer items-center justify-center gap-2 rounded-lg border border-[#d9b87c]/45 text-sm font-semibold text-[#efd9a7] transition-colors duration-200 hover:bg-[#d9b87c]/10 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#efd9a7]"
      >
        <span>{mode.cta}</span>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <path d="M5 12h14" />
          <path d="m12 5 7 7-7 7" />
        </svg>
      </button>
    </article>
  );
}

export default function TrainSection({ onStartTraining }: { onStartTraining: () => void }) {
  return (
    <section id="train" className="relative">
      <DustCanvas />
      <div className="relative z-10 mx-auto w-full max-w-[1400px] px-5 py-28 sm:px-8 lg:py-40 xl:pl-40">
        <div className="grid items-center gap-14 lg:grid-cols-[0.85fr_1.15fr] lg:gap-16">
          <SectionHeading
            label="Train Like Masters"
            title={
              <>
                Every Mode.
                <br />
                One Hub.
              </>
            }
            copy={
              <>
                Opponent prep. Engine sparring.
                <br />
                Endgames. Scenarios.
                <br />
                All of it, in one place.
              </>
            }
            footnote="Four training modes, always ready"
          />

          <div data-reveal data-reveal-delay="0.15" className="relative">
            <div
              aria-hidden
              className="absolute -inset-10 rounded-full bg-[radial-gradient(circle,rgba(217,184,124,0.08),transparent_65%)]"
            />
            <div className="relative grid gap-4 sm:grid-cols-2">
              {MODES.map((mode) => (
                <ModeCardView key={mode.title} mode={mode} onCta={onStartTraining} />
              ))}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
