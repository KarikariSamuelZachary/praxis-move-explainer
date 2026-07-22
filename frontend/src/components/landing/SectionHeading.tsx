import type { ReactNode } from 'react';

type SectionHeadingProps = {
  label: string;
  title: ReactNode;
  copy: ReactNode;
  footnote?: string;
};

export default function SectionHeading({ label, title, copy, footnote }: SectionHeadingProps) {
  return (
    <div className="max-w-xl">
      <p
        data-reveal
        className="text-xs font-semibold uppercase tracking-[0.3em] text-moss-bright"
      >
        {label}
      </p>
      <h2
        data-reveal
        data-reveal-delay="0.08"
        className="mt-6 font-display text-4xl font-semibold leading-[1.16] tracking-wide text-gold-bright sm:text-5xl"
      >
        {title}
      </h2>
      <p
        data-reveal
        data-reveal-delay="0.16"
        className="mt-7 text-base leading-8 text-wood-mute sm:text-lg"
      >
        {copy}
      </p>
      {footnote && (
        <p
          data-reveal
          data-reveal-delay="0.24"
          className="mt-8 flex items-center gap-3 text-sm tracking-wide text-cream/80"
        >
          <span
            aria-hidden
            className="inline-block h-2 w-2 rotate-45 border border-gold bg-gold/30"
          />
          {footnote}
        </p>
      )}
    </div>
  );
}
