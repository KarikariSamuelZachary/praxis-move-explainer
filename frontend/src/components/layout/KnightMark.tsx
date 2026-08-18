export function KnightMark({ className = '' }: { className?: string }) {
  return (
    <div
      aria-hidden
      className={className}
      style={{
        WebkitMaskImage: 'url(/knight-mask.webp)',
        maskImage: 'url(/knight-mask.webp)',
        WebkitMaskSize: 'contain',
        maskSize: 'contain',
        WebkitMaskRepeat: 'no-repeat',
        maskRepeat: 'no-repeat',
        WebkitMaskPosition: 'center',
        maskPosition: 'center',
        background: 'linear-gradient(160deg, #efd9a7 10%, #d9b87c 55%, #a37f45 100%)',
      }}
    />
  );
}

export default KnightMark;