import {
  Show,
  SignInButton,
  SignUpButton,
  UserButton,
} from "@clerk/nextjs";
import Sidebar from "@/components/layout/Sidebar";

export default function AppLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <div className="min-h-screen bg-zinc-950 bg-[radial-gradient(circle_at_top,_rgba(16,185,129,0.08),_rgba(9,9,11,1)_52%)] text-zinc-100">
      <Sidebar />
      <header className="fixed right-4 top-4 z-30 flex items-center gap-2">
        <Show when="signed-out">
          <SignInButton mode="modal">
            <button className="rounded-md border border-zinc-700 bg-zinc-900/90 px-3 py-2 text-sm font-medium text-zinc-100 shadow-lg shadow-black/20 transition hover:border-emerald-500/70 hover:bg-zinc-800">
              Sign in
            </button>
          </SignInButton>
          <SignUpButton mode="modal">
            <button className="rounded-md bg-emerald-500 px-3 py-2 text-sm font-semibold text-zinc-950 shadow-lg shadow-emerald-500/20 transition hover:bg-emerald-400">
              Sign up
            </button>
          </SignUpButton>
        </Show>
        <Show when="signed-in">
          <UserButton />
        </Show>
      </header>
      <main className="min-h-screen overflow-auto md:ml-[220px]">
        {children}
      </main>
    </div>
  );
}
