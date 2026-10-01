import TopNav from "@/components/layout/TopNav";

export default function AppLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <div className="flex h-svh flex-col overflow-hidden text-zinc-100">
      <TopNav />
      {/* Fixed nav occupies h-12: offset content with mt-12 instead of
          padding inside a second h-screen (which clipped ~48px off the
          bottom of every page). The scroll lives here so stacked
          below-xl layouts can always reach their left/right cards;
          xl board pages lock their own inner scroll. */}
      <main className="mt-12 min-h-0 flex-1 overflow-y-auto xl:overflow-hidden">
        {children}
      </main>
    </div>
  );
}
