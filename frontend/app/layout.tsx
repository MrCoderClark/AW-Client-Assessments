import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { AppProvider } from "./_components/app-provider";
import { AppShell } from "./_components/app-shell";
import { AuthProvider } from "./_components/auth-provider";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Client Viewer — Assessments",
  description: "Discovery, indexing, and backup of client assessment PDFs across 24 lab PCs.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    // ponytail: suppressHydrationWarning silences the false-positive from browser
    // extensions (e.g. "Sigcapture") that stamp attrs on <html> before hydration.
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`} suppressHydrationWarning>
      <body>
        <AuthProvider>
          <AppProvider>
            <AppShell>{children}</AppShell>
          </AppProvider>
        </AuthProvider>
      </body>
    </html>
  );
}
