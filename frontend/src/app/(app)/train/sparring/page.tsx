'use client';

import Image from 'next/image';
import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';

import {
  PERSONAS,
  UPCOMING_PERSONAS,
  TIERS,
  type Persona,
  type PersonaKey,
  type PlayColor,
  type TierKey,
} from './personas';

// Persona tones mirror the icons' own palettes and reuse the same tone
// colors the Train landing page assigns to its mode cards.
const TONE_HOVER_BORDER: Record<Persona['tone'], string> = {
  amber: 'hover:border-amber-400/40',
  purple: 'hover:border-purple-400/40',
  blue: 'hover:border-blue-400/40',
  emerald: 'hover:border-emerald-400/40',
  orange: 'hover:border-orange-400/40',
};

const CARD_CLASS =
  'rounded-2xl border border-black/50 backdrop-blur-sm [background-image:linear-gradient(rgba(0,0,0,0.5),rgba(0,0,0,0.5)),url(/walnut-dark.webp)] [background-size:cover] [background-position:center] [box-shadow:0_10px_30px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(255,255,255,0.06),inset_0_-1px_0_rgba(0,0,0,0.5)]';

// Tile width is owned by the grid (equal columns); the tile fills its cell.
const TILE_CLASS = `group relative flex w-full cursor-pointer flex-col items-center gap-2 rounded-2xl border border-black/50 p-3 backdrop-blur-sm transition duration-300 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#efd9a7] motion-safe:hover:-translate-y-1 motion-safe:active:scale-95 [background-image:linear-gradient(rgba(0,0,0,0.5),rgba(0,0,0,0.5)),url(/walnut-dark.webp)] [background-size:cover] [background-position:center] [box-shadow:0_10px_30px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(255,255,255,0.06),inset_0_-1px_0_rgba(0,0,0,0.5)]`;

// Section titles mirror the Praxis logo's typography (font-display, bold,
// wide letter-spacing) one size step down (text-lg vs the logo's text-xl),
// centered; Personas and Bots share the exact same style.
const SECTION_LABEL_CLASS =
  'block text-center font-display text-lg font-bold uppercase tracking-[0.16em] text-[#f7e5c6]/60';

const OPTION_SELECTED_CLASS = 'bg-[#f7e5c6] text-[#241206]';

const OPTION_IDLE_CLASS =
  'border border-black/50 bg-black/40 text-[#f7e5c6]/70 hover:bg-white/10 hover:text-[#f7e5c6]';

function CloseIcon() {
  return (
    <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" aria-hidden="true">
      <path d="M18 6 6 18M6 6l12 12" />
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

type StartConfig = {
  persona: PersonaKey;
  tier: TierKey;
  color: PlayColor;
};

type PersonaStartDialogProps = {
  persona: Persona;
  onClose: () => void;
  onStart: (config: StartConfig) => void;
};

function PersonaStartDialog({ persona, onClose, onStart }: PersonaStartDialogProps) {
  const [tier, setTier] = useState<TierKey | null>(null);
  const [color, setColor] = useState<PlayColor>('white');

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose();
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [onClose]);

  const selectedTier = TIERS.find((entry) => entry.key === tier) ?? null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm px-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={`Start sparring with the ${persona.name} persona`}
    >
      <div
        className={`${CARD_CLASS} relative w-full max-w-md rounded-2xl p-5`}
        onClick={(event) => event.stopPropagation()}
      >
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="absolute right-3 top-3 flex h-8 w-8 items-center justify-center rounded-lg text-[#f7e5c6]/70 transition hover:bg-white/10 hover:text-[#f7e5c6]"
        >
          <CloseIcon />
        </button>

        <div className="flex items-center gap-3">
          <div className="h-12 w-12 shrink-0 overflow-hidden rounded-full ring-1 ring-black/50">
            <Image
              src={persona.icon}
              alt=""
              width={96}
              height={96}
              draggable={false}
              className="h-full w-full select-none object-contain"
            />
          </div>
          <div className="min-w-0">
            <h2 className="font-display text-xl font-semibold text-[#f7e5c6]">
              {persona.name}
            </h2>
            <p className="mt-0.5 text-sm leading-snug text-[#f7e5c6]/60">
              {persona.description}
            </p>
          </div>
        </div>

        <div className="mt-4">
          <span className="text-[11px] font-bold uppercase tracking-[0.25em] text-[#f7e5c6]/60">
            Strength
          </span>
          <div className="mt-1.5 grid grid-cols-2 gap-2">
            {TIERS.map((entry) => {
              const isSelected = tier === entry.key;
              return (
                <button
                  key={entry.key}
                  type="button"
                  onClick={() => setTier(entry.key)}
                  aria-pressed={isSelected}
                  className={`h-10 rounded-[8px] text-sm font-semibold transition ${
                    isSelected ? OPTION_SELECTED_CLASS : OPTION_IDLE_CLASS
                  }`}
                >
                  {entry.name}
                </button>
              );
            })}
          </div>
        </div>

        <div className="mt-3">
          <span className="text-[11px] font-bold uppercase tracking-[0.25em] text-[#f7e5c6]/60">
            Play As
          </span>
          <div className="mt-1.5 grid grid-cols-2 gap-2">
            {(['white', 'black'] as const).map((option) => {
              const isSelected = color === option;
              return (
                <button
                  key={option}
                  type="button"
                  onClick={() => setColor(option)}
                  aria-pressed={isSelected}
                  className={`h-10 rounded-[8px] text-sm font-semibold capitalize transition ${
                    isSelected ? OPTION_SELECTED_CLASS : OPTION_IDLE_CLASS
                  }`}
                >
                  {option}
                </button>
              );
            })}
          </div>
          <p className="mt-1.5 text-[11px] leading-snug text-[#f7e5c6]/40">
            Personas play the same style on either side. Pick whichever color you prefer.
          </p>
        </div>

        <button
          type="button"
          onClick={() => {
            if (!selectedTier) {
              return;
            }
            onStart({ persona: persona.key, tier: selectedTier.key, color });
          }}
          disabled={!selectedTier}
          className="group/cta mt-5 flex h-11 w-full items-center justify-center gap-2 rounded-lg bg-amber-500/20 text-sm font-semibold text-amber-300 ring-1 ring-amber-500/30 transition-colors duration-200 hover:bg-amber-500/30 hover:ring-amber-500/50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#efd9a7] disabled:pointer-events-none disabled:opacity-40"
        >
          <span>Start Sparring</span>
          <ArrowRightIcon />
        </button>
      </div>
    </div>
  );
}

function PersonaTile({
  persona,
  onSelect,
}: {
  persona: Persona;
  onSelect: (persona: Persona) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onSelect(persona)}
      className={`${TILE_CLASS} ${TONE_HOVER_BORDER[persona.tone]}`}
      aria-label={`Start sparring with the ${persona.name} persona`}
    >
      <Image
        src={persona.icon}
        alt=""
        width={192}
        height={192}
        sizes="(min-width: 1024px) 186px, (min-width: 640px) 139px, 157px"
        draggable={false}
        className="h-14 w-14 select-none object-contain transition-transform duration-500 motion-safe:group-hover:scale-[1.05] sm:h-16 sm:w-16"
      />
      <span className="font-display text-xs font-semibold text-[#f7e5c6] sm:text-sm">
        {persona.name}
      </span>
    </button>
  );
}

// Coming-soon slot for a planned persona: same grid footprint as a live
// tile, but non-interactive and visually muted.
function UpcomingPersonaTile({ name, description }: { name: string; description: string }) {
  return (
    <div
      title={description}
      aria-label={`${name} persona (coming soon)`}
      className="relative flex w-full cursor-default flex-col items-center gap-2 rounded-2xl border border-dashed border-[#f7e5c6]/15 bg-black/30 p-3"
    >
      <div
        className="flex h-14 w-14 items-center justify-center rounded-full border border-dashed border-[#f7e5c6]/15 text-lg font-semibold text-[#f7e5c6]/25 sm:h-16 sm:w-16"
        aria-hidden
      >
        ?
      </div>
      <span className="font-display text-xs font-semibold text-[#f7e5c6]/40 sm:text-sm">
        {name}
      </span>
      <span className="text-[10px] font-bold uppercase tracking-[0.2em] text-[#f7e5c6]/30">
        Coming soon
      </span>
    </div>
  );
}

export default function EngineSparringPage() {
  const router = useRouter();
  const [activePersona, setActivePersona] = useState<Persona | null>(null);

  function handleStart(config: StartConfig) {
    const params = new URLSearchParams({
      persona: config.persona,
      tier: config.tier,
      color: config.color,
    });
    router.push(`/train/sparring/play?${params.toString()}`);
  }

  return (
    <div className="relative h-[calc(100vh-3rem)] w-full overflow-y-auto overflow-x-hidden px-6 py-6 text-white lg:px-12 [background-image:url(/walnut-dark.webp)] [background-size:cover] [background-position:center]">
      <div className="mx-auto flex h-full max-w-[1600px] flex-col">
        {/* ROW 1 -- the eight personas: five live + three planned slots.
            On lg+ the row is an EXACT 8-equal-column grid, so the row is
            always completely filled with uniform spacing: each tile is
            (containerWidth - 7*gap) / 8 wide, where containerWidth is
            min(viewport - 96px, 1600px). Smaller screens step down to 4 and
            2 columns so tiles stay tappable. Bot tiles will live on their
            own labeled row below this one once they exist. */}
        <section aria-label="Sparring personas">
          <span className={SECTION_LABEL_CLASS}>Personas</span>
          <div className="mt-2 grid grid-cols-2 gap-3 sm:grid-cols-4 sm:gap-4 lg:grid-cols-8">
            {PERSONAS.map((persona) => (
              <PersonaTile
                key={persona.key}
                persona={persona}
                onSelect={setActivePersona}
              />
            ))}
            {UPCOMING_PERSONAS.map((persona) => (
              <UpcomingPersonaTile
                key={persona.name}
                name={persona.name}
                description={persona.description}
              />
            ))}
          </div>
        </section>

        {/* ROW 2 -- reserved for sparring bots. */}
        <section aria-label="Sparring bots" className="mt-6">
          <span className={SECTION_LABEL_CLASS}>Bots</span>
        </section>
      </div>

      {activePersona && (
        <PersonaStartDialog
          persona={activePersona}
          onClose={() => setActivePersona(null)}
          onStart={handleStart}
        />
      )}
    </div>
  );
}
