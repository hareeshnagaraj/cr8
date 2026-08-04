"use client";

import {useEffect, useRef, useState} from "react";
import {IconCheck, IconClose, IconLink} from "./Icons";
import type {Track} from "./PlayerProvider";

type PublicShare = {
  share_ulid: string;
  created_at: string;
  expires_at: string;
  use_count: number;
};

type MintedShare = {
  url: string;
  expires_at: string;
  share_ulid: string;
};

function expiryCountdown(expiresAt: string, now: number) {
  const remaining = new Date(expiresAt).getTime() - now;
  if (!Number.isFinite(remaining) || remaining <= 0) return "expired";
  const totalMinutes = Math.max(1, Math.ceil(remaining / 60_000));
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return hours > 0 ? `dies in ${hours}h ${minutes}m` : `dies in ${minutes}m`;
}

export function ShareDialog({
  track,
  onClose,
}: {
  track: Track;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const [publicCopied, setPublicCopied] = useState(false);
  const [publicLinks, setPublicLinks] = useState<PublicShare[]>([]);
  const [mintedShare, setMintedShare] = useState<MintedShare | null>(null);
  const [loadingLinks, setLoadingLinks] = useState(true);
  const [minting, setMinting] = useState(false);
  const [revoking, setRevoking] = useState<string | null>(null);
  const [confirmRevoke, setConfirmRevoke] = useState<string | null>(null);
  const [publicError, setPublicError] = useState<string | null>(null);
  const [reloadLinks, setReloadLinks] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const revokeTimer = useRef<number | null>(null);
  const copyTimer = useRef<number | null>(null);
  const publicCopyTimer = useRef<number | null>(null);
  const url =
    typeof window === "undefined"
      ? ""
      : `${window.location.origin}/songs/${track.song_ulid}`;

  // Escape closes, and focus starts inside the dialog rather than wherever the
  // page happened to leave it.
  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    const controller = new AbortController();
    setLoadingLinks(true);
    fetch(`/api/shares?bounce_ulid=${encodeURIComponent(track.bounce_ulid)}`, {
      credentials: "same-origin",
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) throw new Error(`share list failed (${response.status})`);
        const payload = await response.json() as {shares?: PublicShare[]};
        if (!Array.isArray(payload.shares)) throw new Error("share list was invalid");
        setPublicLinks(payload.shares);
        setPublicError(null);
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setPublicError("Could not load public links.");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoadingLinks(false);
      });
    return () => controller.abort();
  }, [reloadLinks, track.bounce_ulid]);

  useEffect(() => {
    let timer: number | undefined;
    const stop = () => {
      if (timer === undefined) return;
      window.clearInterval(timer);
      timer = undefined;
    };
    const start = () => {
      if (document.visibilityState === "hidden" || timer !== undefined) return;
      setNow(Date.now());
      timer = window.setInterval(() => setNow(Date.now()), 30_000);
    };
    const visibilityChanged = () => {
      stop();
      if (document.visibilityState === "visible") start();
    };
    start();
    document.addEventListener("visibilitychange", visibilityChanged);
    window.addEventListener("pageshow", visibilityChanged);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", visibilityChanged);
      window.removeEventListener("pageshow", visibilityChanged);
    };
  }, []);

  useEffect(() => () => {
    if (revokeTimer.current !== null) window.clearTimeout(revokeTimer.current);
    if (copyTimer.current !== null) window.clearTimeout(copyTimer.current);
    if (publicCopyTimer.current !== null) window.clearTimeout(publicCopyTimer.current);
  }, []);

  async function copyMemberLink() {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      if (copyTimer.current !== null) window.clearTimeout(copyTimer.current);
      copyTimer.current = window.setTimeout(() => setCopied(false), 2400);
    } catch {
      window.prompt("Copy this link", url);
    }
  }

  async function mintPublicLink() {
    setMinting(true);
    setPublicError(null);
    try {
      const response = await fetch("/api/shares", {
        method: "POST",
        credentials: "same-origin",
        headers: {"Content-Type": "application/json", "X-CR8-Request": "1"},
        body: JSON.stringify({bounce_ulid: track.bounce_ulid}),
      });
      if (!response.ok) throw new Error(`share mint failed (${response.status})`);
      const created = await response.json() as MintedShare;
      if (!created.url || !created.share_ulid || !created.expires_at) {
        throw new Error("share mint response was invalid");
      }
      setMintedShare(created);
      setPublicCopied(false);
      setReloadLinks((value) => value + 1);
    } catch {
      setPublicError("Could not make a public link.");
    } finally {
      setMinting(false);
    }
  }

  async function copyPublicLink() {
    if (!mintedShare) return;
    try {
      await navigator.clipboard.writeText(mintedShare.url);
      setPublicCopied(true);
      if (publicCopyTimer.current !== null) window.clearTimeout(publicCopyTimer.current);
      publicCopyTimer.current = window.setTimeout(() => setPublicCopied(false), 2400);
    } catch {
      window.prompt("Copy this public link", mintedShare.url);
    }
  }

  async function revokePublicLink(shareUlid: string) {
    setRevoking(shareUlid);
    setPublicError(null);
    try {
      const response = await fetch(
        `/api/shares/${encodeURIComponent(shareUlid)}/revoke`,
        {
          method: "POST",
          credentials: "same-origin",
          headers: {"X-CR8-Request": "1"},
        },
      );
      if (!response.ok) throw new Error(`share revoke failed (${response.status})`);
      setPublicLinks((links) => links.filter((link) => link.share_ulid !== shareUlid));
      setMintedShare((share) => share?.share_ulid === shareUlid ? null : share);
      setConfirmRevoke(null);
    } catch {
      setPublicError("Could not revoke that public link.");
    } finally {
      setRevoking(null);
    }
  }

  function requestRevoke(shareUlid: string) {
    if (confirmRevoke === shareUlid) {
      void revokePublicLink(shareUlid);
      return;
    }
    setConfirmRevoke(shareUlid);
    if (revokeTimer.current !== null) window.clearTimeout(revokeTimer.current);
    revokeTimer.current = window.setTimeout(() => {
      setConfirmRevoke((value) => value === shareUlid ? null : value);
    }, 3000);
  }

  return (
    <div
      className="scrim"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="dialog" role="dialog" aria-modal="true" aria-label="Share">
        <header className="dialog-head">
          <img className="dialog-art" src={`/art/${track.bounce_ulid}`} alt="" />
          <div className="dialog-meta">
            <h2 className="dialog-title">{track.title}</h2>
            <p className="dialog-sub num">
              {[
                track.key_canon,
                track.bpm ? `${Math.round(track.bpm)} bpm` : null,
                track.duration_label,
              ]
                .filter(Boolean)
                .join("  ·  ")}
            </p>
          </div>
          <button
            className="dialog-close"
            onClick={onClose}
            ref={closeRef}
            aria-label="Close"
          >
            <IconClose />
          </button>
        </header>

        <p className="dialog-note">
          Anyone with an account can open this link and hear the track.
        </p>

        <div className="dialog-url">
          <span className="url num">{url}</span>
        </div>

        <div className="dialog-actions">
          <a className="chip" href={`/download/${track.bounce_ulid}?format=mp3`}>
            Download mp3
          </a>
          <button className="primary dialog-copy" onClick={copyMemberLink}>
            {copied ? <IconCheck /> : <IconLink />}
            {copied ? "Copied" : "Copy link"}
          </button>
        </div>

        <section className="public-share" aria-labelledby="public-share-title">
          <div className="public-share-head">
            <div>
              <h3 id="public-share-title">Public link</h3>
              <p>No account needed.</p>
            </div>
            <button
              className="primary public-share-mint"
              onClick={() => void mintPublicLink()}
              disabled={minting}
            >
              {minting ? "Making link…" : "Make a public link · lasts 4 hours"}
            </button>
          </div>

          {mintedShare ? (
            <div className="public-share-url">
              <input
                className="public-share-input num"
                aria-label="New public link"
                value={mintedShare.url}
                readOnly
              />
              <button className="chip" onClick={() => void copyPublicLink()}>
                {publicCopied ? "Copied" : "Copy"}
              </button>
            </div>
          ) : null}

          {publicError ? <p className="public-share-error" role="alert">{publicError}</p> : null}

          <div className="public-share-list" aria-live="polite">
            {loadingLinks ? <p className="public-share-empty">Loading links…</p> : null}
            {!loadingLinks && publicLinks.length === 0 ? (
              <p className="public-share-empty">No active public links.</p>
            ) : null}
            {publicLinks.map((link) => (
              <div className="public-share-row" key={link.share_ulid}>
                <span className="num">{expiryCountdown(link.expires_at, now)}</span>
                <span>{link.use_count} {link.use_count === 1 ? "use" : "uses"}</span>
                <button
                  className="public-share-revoke"
                  onClick={() => requestRevoke(link.share_ulid)}
                  disabled={revoking === link.share_ulid}
                >
                  {revoking === link.share_ulid
                    ? "Revoking…"
                    : confirmRevoke === link.share_ulid ? "Really?" : "Revoke"}
                </button>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
