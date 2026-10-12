import type { Metadata } from "next";
import { ClerkProvider } from "@clerk/nextjs";
import localFont from "next/font/local";
import "./globals.css";

const geistSans = localFont({
  src: "./fonts/Geist-latin.woff2",
  variable: "--font-geist-sans",
  weight: "100 900",
  display: "swap",
});

const geistMono = localFont({
  src: "./fonts/GeistMono-latin.woff2",
  variable: "--font-geist-mono",
  weight: "100 900",
  display: "swap",
});

const cinzel = localFont({
  src: "./fonts/Cinzel-latin.woff2",
  variable: "--font-cinzel",
  weight: "400 700",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Praxis - Train Like Masters",
  description:
    "Praxis is your all-in-one chess training workspace. 5.8 million puzzles, powerful game review, and Woodpecker spaced repetition.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} ${cinzel.variable} h-full antialiased`}
    >
      <body className="min-h-full">
        <ClerkProvider>
          {children}
        </ClerkProvider>
      </body>
    </html>
  );
}
