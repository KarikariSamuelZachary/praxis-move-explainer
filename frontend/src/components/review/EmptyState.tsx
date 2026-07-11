type EmptyStateProps = {
  title: string;
  description: string;
  icon?: React.ReactNode;
};

export default function EmptyState({ title, description, icon }: EmptyStateProps) {
  return (
    <div className="flex h-full w-full items-center justify-center rounded-[24px] border border-dashed border-white/10 [background-image:linear-gradient(rgba(0,0,0,0.55),rgba(0,0,0,0.55)),url(/walnut-dark.png)] [background-size:cover] [background-position:center] p-8">
      <div className="flex max-w-xs flex-col items-center text-center">
        <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-full border border-[#f7e5c6]/30 bg-black/40 text-[#10b981]">
          {icon ?? (
            <svg
              className="h-7 w-7"
              fill="none"
              stroke="currentColor"
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth="1.6"
              viewBox="0 0 24 24"
              aria-hidden
            >
              <path d="M8 20h8" />
              <path d="M10 22h4" />
              <path d="M9 4h6v4H9z" />
              <path d="M12 8v8" />
              <path d="m9 12 3 4 3-4" />
            </svg>
          )}
        </div>
        <h3 className="text-base font-semibold tracking-tight text-white">{title}</h3>
        <p className="mt-2 text-sm leading-6 text-white/60">{description}</p>
      </div>
    </div>
  );
}
