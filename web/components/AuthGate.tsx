"use client";

import {useEffect, useState} from "react";

// Pages that a person reaches before they have an account. Sending these to
// /login would make an invite link impossible to use: the whole point is that
// the recipient has no credentials yet. /setup is the empty-install path.
const PUBLIC_PREFIXES = ["/login", "/join", "/setup"];

function isPublic(pathname: string) {
  return PUBLIC_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}

function isSetupPath(pathname: string) {
  return pathname === "/setup" || pathname.startsWith("/setup/");
}

// Next serves these pages statically, so it has no idea whether you are signed
// in. Without this you get the full app shell with an empty library and no way
// to reach a login page - which is exactly what it looked like: "it just goes to
// the main app screen, there's nowhere to input the stuff".
export function AuthGate({children}: {children: React.ReactNode}) {
  const [state, setState] = useState<"checking" | "in" | "out">("checking");

  useEffect(() => {
    let live = true;
    const openPage = isPublic(window.location.pathname);

    // Any 401 from anywhere in the app means the session is gone. One place
    // handles it so no individual call has to remember.
    const original = window.fetch;
    window.fetch = async (...args) => {
      const response = await original(...args);
      if (response.status === 401 && !isPublic(window.location.pathname)) {
        window.location.href = "/login";
      }
      return response;
    };

    original("/api/needs-setup", {credentials: "same-origin"})
      .then(async (r) => {
        if (!r.ok) return {needs_setup: false};
        return r.json() as Promise<{needs_setup?: boolean}>;
      })
      .catch(() => ({needs_setup: false}))
      .then((setup) => {
        if (!live) return;
        if (setup.needs_setup) {
          if (!isSetupPath(window.location.pathname)) {
            window.location.href = "/setup";
            return;
          }
          setState("in");
          return;
        }
        if (openPage) {
          setState("in");
          return;
        }
        return original("/api/session-check", {credentials: "same-origin"})
          .then((r) => (r.status === 401 ? "out" : "in"))
          .catch(() => "in")
          .then((next) => {
            if (!live) return;
            if (next === "out") {
              window.location.href = "/login";
              return;
            }
            setState("in");
          });
      });

    return () => {
      live = false;
      window.fetch = original;
    };
  }, []);

  if (state === "checking") {
    return <div className="booting">Loading…</div>;
  }
  return <>{children}</>;
}
