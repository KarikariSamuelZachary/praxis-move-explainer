'use client';

import Image from 'next/image';

type PersonaKey = 'attacker' | 'sacrificer' | 'defender' | 'positional';

type Tone = 'amber' | 'purple' | 'blue' | 'emerald';

type Persona = {
  key: PersonaKey;
  name: string;
  icon: string;
  tone: Tone;
};

const PERSONAS: Persona[] = [
  { key: 'attacker', name: 'Attacker', icon: '/persona-attacker.webp', tone: 'amber' },
  { key: 'sacrificer', name: 'Sacrificer', icon: '/persona-sacrificer.webp', tone: 'purple' },
  { key: 'defender', name: 'Defender', icon: '/persona-defender.webp', tone: 'blue' },
  { key: 'positional', name: 'Positional', icon: '/persona-positional.webp', tone: 'emerald' },
];

// Persona tones mirror the icons' own palettes and reuse the same tone
// colors the Train landing page assigns to its mode cards.
const TONE_HOVER_BORDER: Record<Tone, string> = {
  amber: 'hover:border-amber-400/40',
  purple: 'hover:border-purple-400/40',
  blue: 'hover:border-blue-400/40',
  emerald: 'hover:border-emerald-400/40',
};

const TILE_CLASS = `group relative flex w-28 cursor-pointer flex-col items-center gap-2 rounded-2xl border border-black/50 p-3 backdrop-blur-sm transition duration-300 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#efd9a7] motion-safe:hover:-translate-y-1 motion-safe:active:scale-95 [background-image:linear-gradient(rgba(0,0,0,0.5),rgba(0,0,0,0.5)),url(/walnut-dark.webp)] [background-size:cover] [background-position:center] [box-shadow:0_10px_30px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(255,255,255,0.06),inset_0_-1px_0_rgba(0,0,0,0.5)]`;

function PersonaTile({ persona }: { persona: Persona }) {
  return (
    <button
      type="button"
      className={`${TILE_CLASS} ${TONE_HOVER_BORDER[persona.tone]}`}
      aria-label={`Start sparring with the ${persona.name} persona`}
    >
      <Image
        src={persona.icon}
        alt=""
        width={192}
        height={192}
        sizes="(min-width: 640px) 128px, 112px"
        draggable={false}
        className="h-14 w-14 select-none object-contain transition-transform duration-500 motion-safe:group-hover:scale-[1.05] sm:h-16 sm:w-16"
      />
      <span className="font-display text-xs font-semibold text-[#f7e5c6] sm:text-sm">
        {persona.name}
      </span>
    </button>
  );
}

export default function EngineSparringPage() {
  return (
    <div className="relative h-[calc(100vh-3rem)] w-full overflow-y-auto overflow-x-hidden px-6 py-6 text-white lg:px-12 [background-image:url(/walnut-dark.webp)] [background-size:cover] [background-position:center]">
      <div className="mx-auto flex h-full max-w-[1600px] flex-col">
        {/* Fixed-width slots in a left-aligned row: three more persona tiles
            will be appended to this same row later (eventual 7-slot row).
            The tiles must never resize, re-center, or shift when that
            happens -- the space to their right stays empty for now. */}
        <section
          aria-label="Sparring personas"
          className="flex flex-wrap content-start items-start gap-3 sm:gap-4"
        >
          {PERSONAS.map((persona) => (
            <PersonaTile key={persona.key} persona={persona} />
          ))}
        </section>
      </div>
    </div>
  );
}
