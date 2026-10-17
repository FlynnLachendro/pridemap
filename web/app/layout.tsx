import type { Metadata } from "next";
import { Bricolage_Grotesque, Atkinson_Hyperlegible, JetBrains_Mono } from "next/font/google";
import Link from "next/link";
import "./globals.css";
import { Nav } from "@/components/nav";
import { CompareBar } from "@/components/compare-bar";

const display = Bricolage_Grotesque({ variable: "--font-display", subsets: ["latin"], weight: ["500", "700", "800"] });
const body = Atkinson_Hyperlegible({ variable: "--font-body", subsets: ["latin"], weight: ["400", "700"] });
const mono = JetBrains_Mono({ variable: "--font-mono", subsets: ["latin"], weight: ["400", "600"] });

export const metadata: Metadata = {
  title: "PRIDE Map",
  description: "Find the right PRIDE proteomics dataset. LLM-tagged, filterable, explained.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${display.variable} ${body.variable} ${mono.variable} h-full antialiased`}>
      <body className="min-h-full flex flex-col bg-background text-foreground font-sans">
        <Nav />
        <main className="flex-1 w-full max-w-6xl mx-auto px-5 py-8 pb-24">{children}</main>
        <CompareBar />
        <footer className="border-t text-xs text-muted-foreground px-5 py-4 text-center">
          PRIDE Map · Reinvent the Wheel hackathon, Oct 2026 · MIT ·{" "}
          <Link className="underline" href="/about">how it works</Link> ·{" "}
          <a className="underline" href={`${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/docs`}>API docs</a>
        </footer>
      </body>
    </html>
  );
}
