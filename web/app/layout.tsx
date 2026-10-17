import type { Metadata } from "next";
import { Instrument_Sans, Source_Sans_3, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { Shell } from "@/components/nav";
import { CompareBar } from "@/components/compare-bar";
import { Tour } from "@/components/tour";
import { LoadingBar } from "@/components/loading-bar";
import { Suspense } from "react";

const display = Instrument_Sans({ variable: "--font-display", subsets: ["latin"], weight: ["500", "600", "700"] });
const body = Source_Sans_3({ variable: "--font-body", subsets: ["latin"], weight: ["400", "600", "700"] });
const sans = Source_Sans_3({ variable: "--font-sans", subsets: ["latin"], weight: ["400", "600", "700"] });
const mono = JetBrains_Mono({ variable: "--font-mono", subsets: ["latin"], weight: ["400", "500", "600"] });

export const metadata: Metadata = {
  title: "PRIDE Map",
  description: "Find the right PRIDE proteomics dataset. LLM-tagged, filterable, explained.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${display.variable} ${body.variable} ${sans.variable} ${mono.variable} h-full antialiased`}>
      <body className="h-full bg-background text-foreground">
        <LoadingBar />
        <Shell>{children}</Shell>
        <CompareBar />
        <Suspense><Tour /></Suspense>
      </body>
    </html>
  );
}
