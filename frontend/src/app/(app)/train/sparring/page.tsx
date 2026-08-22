'use client';

export default function EngineSparringPage() {
  return (
    <div className="relative -mt-2 flex h-[calc(100vh-2.5rem)] w-full items-center justify-center px-6 text-white [background-image:url(/walnut-dark.webp)] [background-size:cover] [background-position:center]">
      <div className="flex max-w-md flex-col items-center gap-4 rounded-[24px] border border-black/50 bg-black/40 p-10 text-center">
        <h1 className="font-display text-2xl font-semibold text-[#f7e5c6]">
          Engine Sparring
        </h1>
        <p className="text-sm leading-relaxed text-[#f7e5c6]/65">
          Challenge different versions of Stockfish with adjustable strength and
          playstyles. This training mode is coming soon.
        </p>
      </div>
    </div>
  );
}
