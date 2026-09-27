// Lichess knight logomark, sourced from lichess-org/lila (AGPL-3.0), public/logo/lichess.svg.
export function LichessIcon({ className = 'h-5 w-5 shrink-0' }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 50 50" fill="currentColor" stroke="currentColor" strokeLinejoin="round" aria-hidden="true">
      <path d="M38.956.5c-3.53.418-6.452.902-9.286 2.984C5.534 1.786-.692 18.533.68 29.364 3.493 50.214 31.918 55.785 41.329 41.7c-7.444 7.696-19.276 8.752-28.323 3.084S-.506 27.392 4.683 17.567C9.873 7.742 18.996 4.535 29.03 6.405c2.43-1.418 5.225-3.22 7.655-3.187l-1.694 4.86 12.752 21.37c-.439 5.654-5.459 6.112-5.459 6.112-.574-1.47-1.634-2.942-4.842-6.036-3.207-3.094-17.465-10.177-15.788-16.207-2.001 6.967 10.311 14.152 14.04 17.663 3.73 3.51 5.426 6.04 5.795 6.756 0 0 9.392-2.504 7.838-8.927L37.4 7.171z" />
    </svg>
  );
}

// Chess.com mark via Simple Icons (CC0-1.0), recolored with the Chess.com brand green (#81b64c).
// The mark itself is a Chess.com trademark, used here without explicit permission: add a small
// "not affiliated with Chess.com" disclaimer somewhere in the app.
export function ChessComIcon({ className = 'h-5 w-5 shrink-0' }: { className?: string }) {
  return (
    <svg className={`${className} text-[#81b64c]`} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M12 0a3.85 3.85 0 0 0-3.875 3.846A3.84 3.84 0 0 0 9.73 6.969l-2.79 1.85c0 .622.144 1.114.434 1.649H9.83c-.014.245-.014.549-.014.925 0 .025.003.048.006.071-.064 1.353-.507 3.472-3.62 5.842-.816.625-1.423 1.495-1.806 2.533a.33.33 0 0 0-.045.084 8.124 8.124 0 0 0-.39 2.516c0 .1.216 1.561 8.038 1.561s8.038-1.46 8.038-1.561c0-2.227-.824-4.048-2.24-5.133-4.034-3.08-3.586-5.74-3.644-6.838h2.458c.29-.535.434-1.027.434-1.649l-2.79-1.836a3.86 3.86 0 0 0 1.604-3.123A3.873 3.873 0 0 0 13.445.275c-.004-.002-.01.004-.015.004A3.76 3.76 0 0 0 12 0Z" />
    </svg>
  );
}

export function ProviderIcon({
  provider,
  className,
}: {
  provider: 'lichess' | 'chesscom';
  className?: string;
}) {
  return provider === 'lichess' ? (
    <LichessIcon className={className} />
  ) : (
    <ChessComIcon className={className} />
  );
}
