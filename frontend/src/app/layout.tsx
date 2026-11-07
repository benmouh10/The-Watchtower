import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "GlobeLens AI — News Intelligence Platform",
  description:
    "Advanced AI-powered news intelligence platform. Multi-source synthesis, bias detection, geo-tagged events, and real-time fact-checking.",
  keywords: ["news", "AI", "intelligence", "bias detection", "fact-check"],
  authors: [{ name: "GlobeLens AI Team" }],
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link
          rel="preconnect"
          href="https://fonts.gstatic.com"
          crossOrigin="anonymous"
        />
        <link
          rel="stylesheet"
          href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
          integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY="
          crossOrigin=""
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
