import type { Metadata } from "next";
import { Bricolage_Grotesque, Atkinson_Hyperlegible, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { Shell } from "@/components/nav";
import { CompareBar } from "@/components/compare-bar";
import { Tour } from "@/components/tour";
import { Suspense } from "react";

const display = Bricolage_Grotesque({ variable: "--font-display", subsets: ["latin"], weight: ["500", "700", "800"] });
const body = Atkinson_Hyperlegible({ variable: "--font-body", subsets: ["latin"], weight: ["400", "700"] });
const sans = Atkinson_Hyperlegible({ variable: "--font-sans", subsets: ["latin"], weight: ["400", "700"] });
const mono = JetBrains_Mono({ variable: "--font-mono", subsets: ["latin"], weight: ["400", "500", "600"] });

export const metadata: Metadata = {
  title: "PRIDE Map",
  description: "Find the right PRIDE proteomics dataset. LLM-tagged, filterable, explained.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${display.variable} ${body.variable} ${sans.variable} ${mono.variable} h-full antialiased`}>
      <body className="h-full bg-background text-foreground">
        <Shell>{children}</Shell>
        <CompareBar />
        <Suspense><Tour /></Suspense>
      </body>
    </html>
  );
}
