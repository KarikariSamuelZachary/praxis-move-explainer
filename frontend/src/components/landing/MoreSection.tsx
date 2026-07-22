'use client';

const PILLARS = [
  {
    title: 'Lessons',
    copy: 'Structured lessons to guide your journey from beginner to advanced player.',
    icon: (
      <svg viewBox="0 0 48 48" fill="none" stroke="currentColor" strokeWidth="1.4" className="h-12 w-12">
        <path d="M24 10c-4-3-10-4-16-3v30c6-1 12 0 16 3 4-3 10-4 16-3V7c-6-1-12 0-16 3z" />
        <path d="M24 10v30" />
      </svg>
    ),
  },
  {
    title: 'Openings',
    copy: 'Explore opening lines with interactive boards and engine-backed analysis.',
    icon: (
      <svg viewBox="0 0 48 48" fill="none" stroke="currentColor" strokeWidth="1.4" className="h-12 w-12">
        <path d="M24 8c-4-3-10-4-16-3v30c6-1 12 0 16 3 4-3 10-4 16-3V5c-6-1-12 0-16 3z" />
        <path d="M30 14l3 3-8 8-3-3 8-8z" />
        <circle cx="33" cy="15" r="2.5" />
      </svg>
    ),
  },
  {
    title: 'Community',
    copy: 'Connect with players, share games, and grow together.',
    icon: (
      <svg viewBox="0 0 48 48" fill="none" stroke="currentColor" strokeWidth="1.4" className="h-12 w-12">
        <circle cx="17" cy="17" r="6" />
        <circle cx="33" cy="20" r="4.5" />
        <path d="M6 38c0-6 5-10 11-10s11 4 11 10" />
        <path d="M30 31c4 1 8 4 8 8" />
      </svg>
    ),
  },
];

export default function MoreSection() {
  return (
    <section id="more" className="relative">
      <div className="mx-auto w-full max-w-[1400px] px-5 py-28 sm:px-8 lg:py-40 xl:pl-28">
        <div className="grid gap-14 sm:grid-cols-3 sm:gap-8 lg:gap-14">
          {PILLARS.map((pillar, index) => (
            <div
              key={pillar.title}
              data-reveal
              data-reveal-delay={String(index * 0.12)}
              className="group border-t border-white/10 pt-8"
            >
              <div className="text-gold/80 transition-colors duration-500 group-hover:text-gold-bright">
                {pillar.icon}
              </div>
              <h3 className="mt-6 font-display text-2xl font-semibold tracking-wide text-gold-bright">
                {pillar.title}
              </h3>
              <p className="mt-4 max-w-xs text-sm leading-7 text-wood-mute">
                {pillar.copy}
              </p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
