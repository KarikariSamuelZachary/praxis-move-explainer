import { KnightMark } from '@/components/layout/KnightMark';

type ComingSoonProps = {
  title: string;
};

export default function ComingSoon({ title }: ComingSoonProps) {
  return (
    <div className="relative flex h-[calc(100vh-3rem)] w-full items-center justify-center overflow-hidden px-6 text-white [background-image:url(/walnut-dark.webp)] [background-size:cover] [background-position:center]">
      <div className="flex flex-col items-center gap-5 rounded-2xl border border-black/50 bg-black/40 p-10 text-center backdrop-blur-sm [box-shadow:0_10px_30px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(255,255,255,0.06),inset_0_-1px_0_rgba(0,0,0,0.5)]">
        <KnightMark className="h-12 w-10" />
        <h1 className="font-display text-3xl font-semibold tracking-wide text-gold-bright">
          {title}
        </h1>
        <p className="max-w-sm text-sm leading-6 text-wood-mute">
          We&apos;re building this. Coming soon!
        </p>
      </div>
    </div>
  );
}
