import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";
import type { Metadata } from "next";
import type { ReactNode } from "react";
import { ThemedToaster } from "@/components/layout/themed-toaster";
import { themeInitScript } from "@/components/layout/theme-toggle";
import "./globals.css";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "Kalpa - Demand & Inventory Intelligence",
  description: "Probabilistic demand forecasting and inventory decisions for retail supply chains.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning className={`${GeistSans.variable} ${GeistMono.variable}`}>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
      </head>
      <body>
        <Providers>{children}</Providers>
        <ThemedToaster />
      </body>
    </html>
  );
}
