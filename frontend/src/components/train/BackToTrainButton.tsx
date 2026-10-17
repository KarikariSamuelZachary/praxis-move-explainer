import Link from 'next/link';

export default function BackToTrainButton({
  href = '/train',
  label = 'Back to Train',
}: {
  href?: string;
  label?: string;
}) {
  return (
    <Link
      href={href}
      aria-label={label}
      title={label}
      className="absolute left-1 top-4 z-30 flex h-9 w-9 items-center justify-center rounded-lg border border-black/50 bg-black/40 text-[#efd9a7] transition hover:bg-black/60 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#efd9a7]"
    >
      <svg
        width="18"
        height="18"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <path d="m15 18-6-6 6-6" />
      </svg>
    </Link>
  );
}
