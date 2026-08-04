import type {Metadata} from "next";

export const metadata: Metadata = {
  title: "You're invited",
  description: "Someone shared their crate with you on cr8. Pick a login and come listen.",
  openGraph: {
    title: "You're invited to a crate on cr8",
    description:
      "Someone shared their unreleased music archive with you. Open the link, pick a login, and listen.",
  },
  twitter: {
    card: "summary_large_image",
    title: "You're invited to a crate on cr8",
    description:
      "Someone shared their unreleased music archive with you. Open the link, pick a login, and listen.",
  },
};

export default function JoinLayout({children}: {children: React.ReactNode}) {
  return children;
}
