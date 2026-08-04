"use client";

import {createContext, useContext, useState} from "react";

// The letter is the onboarding: four sentences in the owner's voice at the
// top of the library, dismissed once per browser. A tour explains an app;
// this explains the relationship. Copy belongs to the owner — edit the
// string, nothing else.
const LETTER =
  "this is five years of my music, finally in one place. some of it's " +
  "finished, most of it isn't, and i honestly forgot half of it existed. " +
  "play stuff, tag what hits you, leave a note when something doesn't " +
  "land. you can't break anything";

const LetterDismissedContext = createContext(false);

export function LetterDismissedProvider({
  children,
  dismissed,
}: {
  children: React.ReactNode;
  dismissed: boolean;
}) {
  return (
    <LetterDismissedContext.Provider value={dismissed}>
      {children}
    </LetterDismissedContext.Provider>
  );
}

export function useLetterDismissed() {
  return useContext(LetterDismissedContext);
}

export function Letter({dismissed}: {dismissed: boolean}) {
  // In the first paint, not popped in after it: a late-mounting card above
  // the header displaces everything below and the layout-shift budget
  // (0.05) is law. The server renders the letter; a dismissal cookie lets
  // the client's first render agree with it.
  const [open, setOpen] = useState(!dismissed);

  if (!open) return null;

  return (
    <aside className="letter">
      <p className="letter-body">{LETTER}</p>
      <p className="letter-sig">— h</p>
      <button
        className="letter-close"
        aria-label="Dismiss"
        title="Dismiss"
        onClick={() => {
          setOpen(false);
          document.cookie = "cr8_letter=done; path=/; max-age=31536000; samesite=lax";
        }}
      >
        ×
      </button>
    </aside>
  );
}
