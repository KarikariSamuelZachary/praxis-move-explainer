'use client';

import Image from 'next/image';
import Link from 'next/link';
import dynamic from 'next/dynamic';
import { useState } from 'react';
import { useRouter } from 'next/navigation';

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
  'rounded-2xl border border-black/50 backdrop-blur-sm [background-image:linear-gradient(rgba(0,0,0,0.5),rgba(0,0,0,0.5)),url(/walnut-dark.webp)] [background-size:cover] [background-position:center] [box-shadow:0_10px_30px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(255,255,255,0.06),inset_0_-1px_0_rgba(0,0,0,0.5)]';

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
  onClick?: () => void;
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

function CloseIcon() {
  return (
    <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" aria-hidden="true">
      <path d="M18 6 6 18M6 6l12 12" />
    </svg>
  );
}

function SearchIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#f0e0c0" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="11" cy="11" r="7" />
      <line x1="16.5" y1="16.5" x2="21" y2="21" />
    </svg>
  );
}

function ModeIcon({ kind }: { kind: TrainingMode['key'] }) {
  if (kind === 'opponent-prep') return <SwordsIcon />;
  if (kind === 'engine-sparring') return <CrownIcon />;
  if (kind === 'endgame-trainer') return <RookIcon />;
  return <TargetIcon />;
}

type ImportProvider = 'lichess' | 'chesscom';

const IMPORT_PROVIDERS: { key: ImportProvider; label: string }[] = [
  { key: 'lichess', label: 'Lichess' },
  { key: 'chesscom', label: 'Chess.com' },
];

type ImportStartResponse = {
  job_id: string;
  status: string;
  lichess_username?: string | null;
  chesscom_username?: string | null;
  limit: number;
};

type ImportStatusResponse = {
  job_id: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
  lichess_username?: string | null;
  chesscom_username?: string | null;
  requested_limit: number;
  imported_count: number;
  total_games?: number;
  error_message?: string | null;
};

type ApiErrorResponse = {
  detail?: string;
  error?: string;
};

const POLL_INTERVAL_MS = 1500;
const MAX_POLL_ATTEMPTS = 80;
const IMPORT_LIMIT = 200;

function OpponentPrepDialog({ onClose }: { onClose: () => void }) {
  const router = useRouter();
  const [provider, setProvider] = useState<ImportProvider>('lichess');
  const [username, setUsername] = useState('');
  const [phase, setPhase] = useState<'idle' | 'importing' | 'error'>('idle');
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState(0);

  const trimmed = username.trim();
  const isImporting = phase === 'importing';

  async function handleSubmit() {
    if (!trimmed || isImporting) {
      return;
    }

    setPhase('importing');
    setError(null);
    setProgress(0);

    try {
      const startRes = await fetch('/api/train/opponent-import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          [provider === 'lichess' ? 'lichess_username' : 'chesscom_username']: trimmed,
          limit: IMPORT_LIMIT,
        }),
      });
      if (!startRes.ok) {
        const body = (await startRes.json().catch(() => ({}))) as ApiErrorResponse;
        throw new Error(body.detail ?? body.error ?? `Import request failed (${startRes.status})`);
      }
      const startData = (await startRes.json()) as ImportStartResponse;
      const jobId = startData.job_id;

      for (let attempt = 1; attempt <= MAX_POLL_ATTEMPTS; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));

        const pollRes = await fetch(`/api/train/opponent-import/${encodeURIComponent(jobId)}`, {
          cache: 'no-store',
        });
        if (!pollRes.ok) {
          const body = (await pollRes.json().catch(() => ({}))) as ApiErrorResponse;
          throw new Error(body.detail ?? body.error ?? `Status request failed (${pollRes.status})`);
        }
        const pollData = (await pollRes.json()) as ImportStatusResponse;

        if (pollData.status === 'completed') {
          if (pollData.imported_count === 0) {
            throw new Error(
              `No public games found for ${IMPORT_PROVIDERS.find((p) => p.key === provider)?.label} username “${trimmed}”.`
            );
          }
          setProgress(100);
          router.push('/train/sparring');
          onClose();
          return;
        }
        if (pollData.status === 'failed') {
          throw new Error(
            pollData.error_message ?? 'Import failed on the server. Try again in a moment.'
          );
        }
        const total = pollData.total_games || pollData.requested_limit || IMPORT_LIMIT;
        setProgress(Math.min(100, Math.round((pollData.imported_count / total) * 100)));
      }
      throw new Error('Import is taking longer than expected. Close this and try again shortly.');
    } catch (err) {
      setPhase('error');
      setError(err instanceof Error ? err.message : 'Failed to import opponent.');
    }
  }

  function handleClose() {
    if (isImporting) {
      return;
    }
    onClose();
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm px-4"
      onClick={handleClose}
      role="dialog"
      aria-modal="true"
      aria-label="Import an opponent"
    >
      <div
        className={`${CARD_CLASS} relative w-full max-w-md rounded-2xl p-5`}
        onClick={(event) => event.stopPropagation()}
      >
        <button
          type="button"
          onClick={handleClose}
          disabled={isImporting}
          aria-label="Close"
          className="absolute right-3 top-3 flex h-8 w-8 items-center justify-center rounded-lg text-[#f7e5c6]/70 transition hover:bg-white/8 hover:text-[#f7e5c6] disabled:opacity-40 disabled:pointer-events-none"
        >
          <CloseIcon />
        </button>

        <h2 className="font-display text-xl font-semibold text-[#f7e5c6]">Import an opponent</h2>
        <p className="mt-1 text-sm text-[#f7e5c6]/60">
          Fetch a public Lichess or Chess.com player. We&apos;ll train a clone on up to {IMPORT_LIMIT} of their recent games.
        </p>

        <div className="mt-4 grid grid-cols-2 overflow-hidden rounded-[8px] border border-black/50 bg-black/50 p-1">
          {IMPORT_PROVIDERS.map((entry) => (
            <button
              key={entry.key}
              type="button"
              onClick={() => setProvider(entry.key)}
              disabled={isImporting}
              className={`h-10 rounded-[6px] text-sm font-semibold transition disabled:opacity-60 ${
                provider === entry.key
                  ? 'bg-[#f7e5c6] text-[#241206]'
                  : 'text-[#f7e5c6]/70 hover:bg-white/8'
              }`}
            >
              {entry.label}
            </button>
          ))}
        </div>

        <div className="mt-3 flex gap-2">
          <input
            type="text"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault();
                handleSubmit();
              }
            }}
            placeholder={`${IMPORT_PROVIDERS.find((p) => p.key === provider)?.label} username`}
            disabled={isImporting}
            className="min-w-0 flex-1 rounded-xl border border-black/50 bg-black/60 px-3 py-2 text-sm text-white outline-none transition placeholder:text-white/30 focus:border-emerald-400/60 focus:ring-2 focus:ring-emerald-400/20 disabled:opacity-60"
          />
          <button
            type="button"
            onClick={handleSubmit}
            disabled={!trimmed || isImporting}
            aria-label="Import opponent"
            className="flex h-[38px] w-[38px] shrink-0 items-center justify-center rounded-xl border border-black/50 bg-black/60 transition-transform hover:scale-105 active:scale-95 disabled:pointer-events-none disabled:opacity-40"
          >
            {isImporting ? (
              <span className="h-3 w-3 rounded-full border-2 border-[#f0e0c0]/40 border-t-[#f0e0c0] animate-spin" />
            ) : (
              <SearchIcon />
            )}
          </button>
        </div>

        {isImporting && (
          <div className="mt-3 flex items-center gap-3">
            <div className="h-2 flex-1 overflow-hidden rounded-full bg-black/60">
              <div
                className="h-full rounded-full bg-emerald-500 transition-[width] duration-500 ease-out"
                style={{ width: `${progress}%` }}
              />
            </div>
            <span className="w-10 shrink-0 text-right text-xs font-semibold text-emerald-300">
              {progress}%
            </span>
          </div>
        )}

        {error && phase === 'error' && (
          <p
            role="alert"
            className="mt-3 rounded-xl border border-red-400/30 bg-red-400/10 px-3 py-2 text-xs text-red-300"
          >
            {error}
          </p>
        )}
      </div>
    </div>
  );
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
      ) : mode.onClick ? (
        <button type="button" onClick={mode.onClick} className={ctaClass}>
          <span>{mode.cta}</span>
          <ArrowRightIcon />
        </button>
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
                backgroundImage: 'url(/walnut-dark.webp)',
                backgroundSize: '140% 140%',
                backgroundPosition: 'center',
              },
              lightSquareStyle: {
                backgroundImage: 'url(/walnut-light.webp)',
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
  const [isOpponentPrepOpen, setIsOpponentPrepOpen] = useState(false);

  const wiredModes = TRAINING_MODES.map((mode) =>
    mode.key === 'opponent-prep'
      ? { ...mode, onClick: () => setIsOpponentPrepOpen(true) }
      : mode
  );

  return (
    <div className="relative h-[calc(100vh-3rem)] w-full overflow-hidden px-6 py-6 text-white lg:px-12 [background-image:url(/walnut-dark.webp)] [background-size:cover] [background-position:center]">
      <div className="mx-auto flex h-full max-w-[1600px] flex-col justify-center gap-5">
        <RecommendedPanel />

        <section className="grid grid-cols-1 gap-5 sm:grid-cols-2 xl:grid-cols-4" aria-label="Training modes">
          {wiredModes.map((mode) => (
            <TrainingModeCard key={mode.key} mode={mode} />
          ))}
        </section>
      </div>

      {isOpponentPrepOpen && (
        <OpponentPrepDialog onClose={() => setIsOpponentPrepOpen(false)} />
      )}
    </div>
  );
}