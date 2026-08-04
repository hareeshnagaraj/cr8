"use client";

import {useEffect, useState} from "react";
import {useParams, useRouter} from "next/navigation";

type Check =
  | {status: "checking"}
  | {status: "bad"; reason: string}
  | {status: "ok"; role: string; label: string};

const WRITE = {"Content-Type": "application/json", "X-CR8-Request": "1"};

const REASONS: Record<string, string> = {
  revoked: "This invite was revoked.",
  expired: "This invite has expired.",
  exhausted: "This invite has already been used.",
  unknown: "This link is not valid.",
};

export default function Join() {
  const params = useParams<{token: string}>();
  const token = String(params?.token ?? "");
  const router = useRouter();

  const [check, setCheck] = useState<Check>({status: "checking"});
  const [username, setUsername] = useState("");
  const [display, setDisplay] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!token) return;
    fetch(`/api/join/${encodeURIComponent(token)}`, {
      credentials: "same-origin",
    })
      .then(async (response) => {
        const data = await response.json().catch(() => ({}));
        if (response.ok && data.usable) {
          setCheck({status: "ok", role: data.role, label: data.label ?? ""});
        } else {
          setCheck({status: "bad", reason: data.reason ?? "unknown"});
        }
      })
      .catch(() => setCheck({status: "bad", reason: "unknown"}));
  }, [token]);

  async function claim(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const response = await fetch("/api/join", {
        method: "POST",
        credentials: "same-origin",
        headers: WRITE,
        body: JSON.stringify({token, username, display, password}),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail ?? "could not create the account");
      // Already signed in by the server. A song invite carries a same-origin
      // destination; ordinary invites keep landing at the crate root.
      const destination =
        typeof data.redirect === "string" && data.redirect.startsWith("/")
          ? data.redirect
          : "/";
      window.location.href = destination;
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : "something went wrong");
      setBusy(false);
    }
  }

  return (
    <div className="join-wrap">
      <div className="join-card">
        <div className="join-brand">cr8</div>

        {check.status === "checking" ? (
          <p className="join-note">Checking your invite…</p>
        ) : null}

        {check.status === "bad" ? (
          <>
            <h1 className="join-title">This link is closed</h1>
            <p className="join-note">
              {REASONS[check.reason] ?? REASONS.unknown} Ask whoever sent it for
              a new one.
            </p>
            <button className="btn" onClick={() => router.push("/login")}>
              Go to sign in
            </button>
          </>
        ) : null}

        {check.status === "ok" ? (
          <>
            <h1 className="join-title">Pick your login</h1>
            <p className="join-note">
              You are joining as {check.role === "owner" ? "an admin" : "a member"}
              . Choose a name and a password only you know.
            </p>
            <form onSubmit={claim} className="join-form">
              <label className="field">
                <span className="field-label">Username</span>
                <input
                  className="input"
                  value={username}
                  autoCapitalize="none"
                  autoCorrect="off"
                  onChange={(e) => setUsername(e.target.value)}
                  required
                />
              </label>
              <label className="field">
                <span className="field-label">Display name</span>
                <input
                  className="input"
                  value={display}
                  onChange={(e) => setDisplay(e.target.value)}
                  placeholder={username}
                />
              </label>
              <label className="field">
                <span className="field-label">
                  Password <span className="field-hint">12 characters or more</span>
                </span>
                <input
                  className="input"
                  type="password"
                  value={password}
                  minLength={12}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                />
              </label>
              {error ? <p className="join-error">{error}</p> : null}
              <button className="btn btn-main btn-wide" disabled={busy}>
                {busy ? "Setting up…" : "Come in"}
              </button>
            </form>
          </>
        ) : null}
      </div>
    </div>
  );
}
