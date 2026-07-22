'use client';

import TacticBoard from './TacticBoard';
import SectionHeading from './SectionHeading';

export default function PuzzlesSection() {
  return (
    <section id="puzzles" className="relative">
      <div className="mx-auto grid w-full max-w-[1400px] items-center gap-14 px-5 py-28 sm:px-8 lg:grid-cols-[0.9fr_1.1fr] lg:gap-20 lg:py-40 xl:pl-28">
        <SectionHeading
          label="Tactical Training"
          title={
            <>
              5.8 Million
              <br />
              Real Positions.
              <br />
              Endless Growth.
            </>
          }
          copy={
            <>
              Curated from real games.
              <br />
              Organized by themes.
              <br />
              Built for serious improvement.
            </>
          }
          footnote="64 Tactical Themes"
        />

        <div data-reveal data-reveal-delay="0.15" className="relative">
          <div
            aria-hidden
            className="absolute -inset-10 rounded-full bg-[radial-gradient(circle,rgba(217,184,124,0.08),transparent_65%)]"
          />
          <div className="mx-auto w-full max-w-[600px]">
            <TacticBoard />
          </div>
        </div>
      </div>
    </section>
  );
}
