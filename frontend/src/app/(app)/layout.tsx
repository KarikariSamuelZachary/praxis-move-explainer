import TopNav from "@/components/layout/TopNav";

export default function AppLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <div className="h-screen overflow-hidden text-zinc-100">
      <TopNav />
      <main className="h-screen overflow-hidden pt-16">
        {children}
      </main>
    </div>
  );
}
