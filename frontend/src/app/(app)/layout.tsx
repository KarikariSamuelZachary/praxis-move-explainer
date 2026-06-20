import TopNav from "@/components/layout/TopNav";

export default function AppLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <div className="min-h-screen bg-zinc-950 bg-[radial-gradient(circle_at_top,_rgba(16,185,129,0.08),_rgba(9,9,11,1)_52%)] text-zinc-100">
      <TopNav />
      <main className="min-h-screen overflow-auto pt-16">
        {children}
      </main>
    </div>
  );
}
