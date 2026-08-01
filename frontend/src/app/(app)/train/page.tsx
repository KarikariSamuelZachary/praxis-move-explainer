'use client';

import Image from 'next/image';
import Link from 'next/link';
import dynamic from 'next/dynamic';

const MiniBoard = dynamic(
  () => import('react-chessboard').then((module) => module.Chessboard),
  {
    ssr: false,
    loading: () => (
      <div className="h-full w-full animate-pulse rounded-md bg-black/35" aria-hidden="true" />
    ),
  }
);

const CARD_CLASS =
  'rounded-2xl border border-black/50 backdrop-blur-sm [background-image:linear-gradient(rgba(0,0,0,0.5),rgba(0,0,0,0.5)),url(/walnut-dark.png)] [background-size:cover] [background-position:center] [box-shadow:0_10px_30px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(255,255,255,0.06),inset_0_-1px_0_rgba(0,0,0,0.5)]';

const RECOMMENDED_FEN = '1K1k4/1P6/8/8/8/8/r7/2R5 w - - 0 1';

type Tone = 'emerald' | 'amber' | 'purple' | 'blue';

type TrainingMode = {
  key: string;
  title: string;
  description: string;
  illustration: string;
  cta: string;
  tone: Tone;
  href?: string;
};

const TRAINING_MODES: TrainingMode[] = [
  {
    key: 'opponent-prep',
    title: 'Opponent\nPreparation',
    description: 'Enter any Lichess or Chess.com username and face an AI version of their playing style.',
    illustration: '/opponent-prep-illustration.webp',
    cta: 'Start Opponent Prep',
    tone: 'emerald',
  },
  {
    key: 'engine-sparring',
    title: 'Engine\nSparring',
    description: 'Challenge different versions of Stockfish with adjustable strength and playstyles.',
    illustration: '/engine-sparring-illustration.webp',
    cta: 'Choose Engine',
    tone: 'amber',
    href: '/train/sparring',
  },
  {
    key: 'endgame-trainer',
    title: 'Endgame\nTrainer',
    description: 'Master fundamental endgames through focused, position-based practice.',
    illustration: '/endgame-trainer-illustration.webp',
    cta: 'Explore Endgames',
    tone: 'purple',
  },
  {
    key: 'scenario-trainer',
    title: 'Scenario\nTrainer',
    description: 'Practice critical positions, converts, defenses, traps and more game situations.',
    illustration: '/scenario-trainer-illustration.webp',
    cta: 'Browse Scenarios',
    tone: 'blue',
  },
];

const TONE_STYLES: Record<
  Tone,
  { ring: string; iconBg: string; iconText: string; button: string; buttonText: string }
> = {
  emerald: {
    ring: 'ring-emerald-400/50',
    iconBg: 'bg-emerald-500/35',
    iconText: 'text-emerald-300',
    button: 'bg-emerald-500/20 hover:bg-emerald-500/30 ring-1 ring-emerald-500/30 hover:ring-emerald-500/50',
    buttonText: 'text-emerald-300',
  },
  amber: {
    ring: 'ring-amber-400/50',
    iconBg: 'bg-amber-500/35',
    iconText: 'text-amber-300',
    button: 'bg-amber-500/20 hover:bg-amber-500/30 ring-1 ring-amber-500/30 hover:ring-amber-500/50',
    buttonText: 'text-amber-300',
  },
  purple: {
    ring: 'ring-purple-400/50',
    iconBg: 'bg-purple-500/35',
    iconText: 'text-purple-300',
    button: 'bg-purple-500/20 hover:bg-purple-500/30 ring-1 ring-purple-500/30 hover:ring-purple-500/50',
    buttonText: 'text-purple-300',
  },
  blue: {
    ring: 'ring-blue-400/50',
    iconBg: 'bg-blue-500/35',
    iconText: 'text-blue-300',
    button: 'bg-blue-500/20 hover:bg-blue-500/30 ring-1 ring-blue-500/30 hover:ring-blue-500/50',
    buttonText: 'text-blue-300',
  },
};

function SwordsIcon() {
  return (
    <svg className="h-7 w-7" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" aria-hidden="true">
      <path d="M14.5 17.5 3 6V3h3l11.5 11.5" />
      <path d="M13 19l6-6" />
      <path d="M16 16l4 4" />
      <path d="M19 21l2-2" />
      <path d="M14.5 6.5 21 3v3l-3.5 3.5" />
      <path d="M5 14l4 4" />
      <path d="M7 16l-2 2" />
      <path d="M3 19l-1 1" />
    </svg>
  );
}

function CrownIcon() {
  return (
    <svg className="h-7 w-7" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" aria-hidden="true">
      <path d="M3 7l4 5 5-7 5 7 4-5-1 12H4L3 7z" />
      <path d="M5 21h14" />
      <path d="M7 4.5l1 1.5" />
      <path d="M17 4.5l-1 1.5" />
      <path d="M12 4v2" />
    </svg>
  );
}

function RookIcon() {
  return (
    <svg className="h-7 w-7" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" aria-hidden="true">
      <path d="M5 8V4h2v1h2V4h2v1h2V4h2v1h2V4h2v4" />
      <path d="M5 8v10h14V8" />
      <path d="M3 20h18" />
      <path d="M5 18h14v2H5z" />
    </svg>
  );
}

function TargetIcon() {
  return (
    <svg className="h-7 w-7" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <circle cx="12" cy="12" r="5" />
      <circle cx="12" cy="12" r="1.4" fill="currentColor" />
      <path d="M12 2v3M12 19v3M2 12h3M19 12h3" />
    </svg>
  );
}

function ArrowRightIcon() {
  return (
    <svg className="h-4 w-4 transition-transform duration-200 group-hover/cta:translate-x-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" aria-hidden="true">
      <path d="M5 12h14" />
      <path d="m12 5 7 7-7 7" />
    </svg>
  );
}

function StarIcon() {
  return (
    <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" aria-hidden="true">
      <path d="m12 3 2.7 5.6 6.1.8-4.5 4.3 1.1 6.1L12 17l-5.4 2.8 1.1-6.1L3.2 9.4l6.1-.8L12 3z" />
    </svg>
  );
}

function ModeIcon({ kind }: { kind: TrainingMode['key'] }) {
  if (kind === 'opponent-prep') return <SwordsIcon />;
  if (kind === 'engine-sparring') return <CrownIcon />;
  if (kind === 'endgame-trainer') return <RookIcon />;
  return <TargetIcon />;
}

function TrainingModeCard({ mode }: { mode: TrainingMode }) {
  const tone = TONE_STYLES[mode.tone];
  const ctaClass = `group/cta relative mt-3 flex h-10 w-full cursor-pointer items-center justify-center gap-2 rounded-lg text-sm font-semibold ring-1 transition-colors duration-200 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#efd9a7] ${tone.button} ${tone.buttonText}`;

  return (
    <article
      className={`${CARD_CLASS} group relative flex min-h-[18rem] flex-col overflow-hidden p-4 shadow-2xl shadow-black/30 transition duration-300 hover:border-[#d9b87c]/30 motion-safe:hover:-translate-y-1`}
    >
      <Image
        src={mode.illustration}
        alt=""
        fill
        sizes="(min-width: 1280px) 25vw, (min-width: 640px) 50vw, 100vw"
        className="select-none object-cover object-center opacity-30 transition-transform duration-500 motion-safe:group-hover:scale-[1.04]"
      />
      <div
        className="pointer-events-none absolute inset-0 bg-gradient-to-b from-black/60 via-black/40 to-black/75"
        aria-hidden="true"
      />

      <div className="relative flex items-center gap-3">
        <div
          className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-full ring-1 transition duration-300 ${tone.ring} ${tone.iconBg} ${tone.iconText}`}
        >
          <ModeIcon kind={mode.key} />
        </div>
        <h3 className="whitespace-pre-line font-display text-2xl font-semibold leading-tight text-[#f7e5c6]">
          {mode.title}
        </h3>
      </div>

      <p className="relative mt-3 text-sm leading-relaxed text-white/70">{mode.description}</p>

      <div className="relative flex-1" />

      {mode.href ? (
        <Link href={mode.href} className={ctaClass}>
          <span>{mode.cta}</span>
          <ArrowRightIcon />
        </Link>
      ) : (
        <button type="button" className={ctaClass}>
          <span>{mode.cta}</span>
          <ArrowRightIcon />
        </button>
      )}
    </article>
  );
}

function RecommendedPanel() {
  return (
    <section className={`${CARD_CLASS} flex flex-col gap-2 p-3 self-end shadow-2xl shadow-black/25`} aria-label="Recommended for you">
      <header className="flex items-center gap-2">
        <span className="text-[#f7e5c6]/70">
          <StarIcon />
        </span>
        <h2 className="text-[10px] font-bold uppercase tracking-[0.18em] text-[#f7e5c6]/65">
          Recommended For You
        </h2>
      </header>

      <div className="flex items-center gap-3">
        <div className="h-20 w-20 shrink-0 overflow-hidden rounded-lg shadow-lg shadow-black/50 ring-1 ring-black/60">
          <MiniBoard
            options={{
              position: RECOMMENDED_FEN,
              allowDragging: false,
              showNotation: false,
              darkSquareStyle: {
                backgroundImage: 'url(/walnut-dark.png)',
                backgroundSize: '140% 140%',
                backgroundPosition: 'center',
              },
              lightSquareStyle: {
                backgroundImage: 'url(/walnut-light.png)',
                backgroundSize: '140% 140%',
                backgroundPosition: 'center',
              },
              boardStyle: { width: '100%', height: '100%' },
            }}
          />
        </div>
        <h3 className="min-w-0 flex-1 font-display text-lg font-semibold leading-snug text-[#f7e5c6]">
          Strengthen your endgames
        </h3>
      </div>

      <button
        type="button"
        className="group/cta inline-flex h-9 w-full cursor-pointer items-center justify-center gap-2 rounded-lg border border-[#d9b87c]/45 px-4 text-sm font-semibold text-[#efd9a7] transition-colors duration-200 hover:bg-[#d9b87c]/10 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#efd9a7]"
      >
        <span>Start Rook Endings</span>
        <ArrowRightIcon />
      </button>
    </section>
  );
}

export default function TrainPage() {
  return (
    <div className="relative h-[calc(100vh-2.75rem)] w-full overflow-hidden px-6 py-6 text-white lg:px-12 [background-image:url(/walnut-dark.png)] [background-size:cover] [background-position:center]">
      <div className="mx-auto flex h-full max-w-[1600px] flex-col justify-center gap-5">
        <RecommendedPanel />

        <section className="grid grid-cols-1 gap-5 sm:grid-cols-2 xl:grid-cols-4" aria-label="Training modes">
          {TRAINING_MODES.map((mode) => (
            <TrainingModeCard key={mode.key} mode={mode} />
          ))}
        </section>
      </div>
    </div>
  );
}
