import type {Metadata, Viewport} from "next";
import {cookies} from "next/headers";
import "./globals.css";
import {AuthGate} from "@/components/AuthGate";
import {LetterDismissedProvider} from "@/components/Letter";
import {PlayerProvider} from "@/components/PlayerProvider";
import {Shell} from "@/components/Shell";

const SITE = "cr8";
const TITLE = "cr8 — a private room for unreleased music";
const DESCRIPTION =
  "Self-hosted open source listening for original music. Not a SaaS. Hear the archive, tag while it plays, send a link that works.";
// Absolute OG URLs for iMessage/Slack. Prefer public origin; local dev still works.
const SITE_URL =
  process.env.NEXT_PUBLIC_SITE_URL?.replace(/\/$/, "") || "https://cr8.li";

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: {
    default: TITLE,
    template: `%s · ${SITE}`,
  },
  description: DESCRIPTION,
  applicationName: SITE,
  openGraph: {
    type: "website",
    siteName: SITE,
    title: TITLE,
    description: DESCRIPTION,
    url: SITE_URL,
    images: [
      {
        url: "/og.png",
        width: 1200,
        height: 630,
        alt: TITLE,
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: TITLE,
    description: DESCRIPTION,
    images: ["/og.png"],
  },
};

// viewportFit=cover lets the tab bar sit against the bottom of the screen and
// pad itself out of the home indicator's way, rather than floating above a
// white strip. maximumScale is not pinned: pinching to read a title is nobody's
// enemy.
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: "#0d0d0f",
};

export default async function RootLayout({children}: {children: React.ReactNode}) {
  const letterDismissed = (await cookies()).get("cr8_letter")?.value === "done";

  return (
    <html lang="en">
      <body>
        {/* The provider owns the one <audio> element for the whole session. It
            lives above the router, so navigating never unmounts it and playback
            never stops. That is the entire reason for this port. */}
        <LetterDismissedProvider dismissed={letterDismissed}>
          <AuthGate>
            <PlayerProvider>
              <Shell>{children}</Shell>
            </PlayerProvider>
          </AuthGate>
        </LetterDismissedProvider>
      </body>
    </html>
  );
}
