'use client';

import dynamic from 'next/dynamic';

import SectionHeading from './SectionHeading';
import DustCanvas from './DustCanvas';

// ReviewDemo statically imports chess.js + react-chessboard; keep that
// ~60KB gzipped out of the landing page's initial bundle.
const ReviewDemo = dynamic(() => import('./ReviewDemo'));

export default function ReviewSection() {
  return (
    <section id="review" className="relative">
      <DustCanvas />
      <div className="relative z-10 mx-auto grid w-full max-w-[1400px] items-center gap-14 px-5 py-28 sm:px-8 lg:grid-cols-[0.75fr_1.25fr] lg:gap-16 lg:py-40 xl:pl-40">
        <SectionHeading
          label="Analyze. Understand. Improve."
          title={
            <>
              Review Games.
              <br />
              Learn Faster.
            </>
          }
          copy={
            <>
              Paste PGN. Import from chess.com or lichess.
              <br />
              Get engine insights.
              <br />
              AI explanations that make sense.
            </>
          }
          footnote="Deep, actionable feedback"
        />

        <div data-reveal data-reveal-delay="0.15" className="relative">
          <div
            aria-hidden
            className="absolute -inset-10 rounded-full bg-[radial-gradient(circle,rgba(55,190,126,0.06),transparent_65%)]"
          />
          <ReviewDemo />
        </div>
      </div>
    </section>
  );
}
