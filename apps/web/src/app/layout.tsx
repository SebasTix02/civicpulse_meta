import type { Metadata, Viewport } from "next";
import { currentLang } from "@/lib/lang-server";
import { AuthForward } from "./AuthForward";
import "./globals.css";

export const metadata: Metadata = {
  title: "CivicPulse",
  description: "Citizen reports verified with Meta's SAM 3.1 and Muse Spark.",
  manifest: "/manifest.webmanifest",
  icons: { icon: "/icon.svg" },
};

export const viewport: Viewport = { themeColor: "#ffffff", width: "device-width", initialScale: 1 };

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const lang = await currentLang();
  return (
    <html lang={lang}>
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet" />
      </head>
      <body>
        <AuthForward />
        {children}
      </body>
    </html>
  );
}
