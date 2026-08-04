"use client";

import {useEffect, useRef, useState} from "react";
import {IconCheck, IconClose, IconLink} from "./Icons";

type CollectionShare = {
  share_ulid: string;
  created_at: string;
  expires_at: string;
  use_count: number;
  diverged: boolean;
};

type MintedShare = {
  url: string;
  expires_at: string;
  share_ulid: string;
};

function expiryCountdown(expiresAt: string, now: number) {
  const remaining = new Date(expiresAt).getTime() - now;
  if (!Number.isFinite(remaining) || remaining <= 0) return "expired";
  const hours = Math.max(1, Math.ceil(remaining / 3_600_000));
  if (hours < 24) return `expires in ${hours}h`;
  const days = Math.ceil(hours / 24);
  return `expires in ${days}d`;
}

async function responseError(response: Response, fallback: string) {
  try {
    const payload = await response.json() as {detail?: unknown};
    if (typeof payload.detail === "string" && payload.detail) return payload.detail;
  } catch {
    // A plain or empty error response still gets useful UI copy below.
  }
  return fallback;
}

export function CollectionShareDialog({
  collectionUlid,
  collectionName,
  onClose,
}: {
  collectionUlid: string;
  collectionName: string;
  onClose: () => void;
}) {
  const [ttlHours, setTtlHours] = useState<24 | 168>(168);
  const [note, setNote] = useState("");
  const [links, setLinks] = useState<CollectionShare[]>([]);
  const [minted, setMinted] = useState<MintedShare | null>(null);
  const [copied, setCopied] = useState(false);
  const [loading, setLoading] = useState(true);
  const [minting, setMinting] = useState(false);
  const [reminting, setReminting] = useState(false);
  const [revoking, setRevoking] = useState<string | null>(null);
  const [confirmRevoke, setConfirmRevoke] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const revokeTimer = useRef<number | null>(null);
  const copyTimer = useRef<number | null>(null);

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
    setLoading(true);
    fetch(`/api/shares?collection_ulid=${encodeURIComponent(collectionUlid)}`, {
      credentials: "same-origin",
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(await responseError(response, "Could not load links."));
        }
        const payload = await response.json() as {shares?: CollectionShare[]};
        if (!Array.isArray(payload.shares)) throw new Error("Could not load links.");
        setLinks(payload.shares);
        setError(null);
      })
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        setError(reason instanceof Error ? reason.message : "Could not load links.");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [collectionUlid, reload]);

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
  }, []);

  async function requestMint() {
    const response = await fetch("/api/shares", {
      method: "POST",
      credentials: "same-origin",
      headers: {"Content-Type": "application/json", "X-CR8-Request": "1"},
      body: JSON.stringify({
        collection_ulid: collectionUlid,
        ttl_hours: ttlHours,
        note,
      }),
    });
    if (!response.ok) {
      throw new Error(await responseError(response, "Could not make this link."));
    }
    const created = await response.json() as MintedShare;
    if (!created.url || !created.share_ulid || !created.expires_at) {
      throw new Error("The new link response was invalid.");
    }
    return created;
  }

  async function mintLink() {
    setMinting(true);
    setError(null);
    try {
      const created = await requestMint();
      setMinted(created);
      setCopied(false);
      setReload((value) => value + 1);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not make this link.");
    } finally {
      setMinting(false);
    }
  }

  async function copyLink() {
    if (!minted) return;
    try {
      await navigator.clipboard.writeText(minted.url);
      setCopied(true);
      if (copyTimer.current !== null) window.clearTimeout(copyTimer.current);
      copyTimer.current = window.setTimeout(() => setCopied(false), 2400);
    } catch {
      window.prompt("Copy this link", minted.url);
    }
  }

  async function revokeLink(shareUlid: string) {
    setRevoking(shareUlid);
    setError(null);
    try {
      const response = await fetch(
        `/api/shares/${encodeURIComponent(shareUlid)}/revoke`,
        {
          method: "POST",
          credentials: "same-origin",
          headers: {"X-CR8-Request": "1"},
        },
      );
      if (!response.ok) {
        throw new Error(await responseError(response, "Could not revoke that link."));
      }
      setLinks((current) => current.filter((link) => link.share_ulid !== shareUlid));
      setMinted((current) => current?.share_ulid === shareUlid ? null : current);
      setConfirmRevoke(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not revoke that link.");
    } finally {
      setRevoking(null);
    }
  }

  function requestRevoke(shareUlid: string) {
    if (confirmRevoke === shareUlid) {
      void revokeLink(shareUlid);
      return;
    }
    setConfirmRevoke(shareUlid);
    if (revokeTimer.current !== null) window.clearTimeout(revokeTimer.current);
    revokeTimer.current = window.setTimeout(() => {
      setConfirmRevoke((current) => current === shareUlid ? null : current);
    }, 3000);
  }

  async function remintLink() {
    const stale = links.find((link) => link.diverged);
    if (!stale) return;
    setReminting(true);
    setError(null);
    try {
      const revoked = await fetch(
        `/api/shares/${encodeURIComponent(stale.share_ulid)}/revoke`,
        {
          method: "POST",
          credentials: "same-origin",
          headers: {"X-CR8-Request": "1"},
        },
      );
      if (!revoked.ok) {
        throw new Error(await responseError(revoked, "Could not revoke the old link."));
      }
      setLinks((current) => current.filter(
        (link) => link.share_ulid !== stale.share_ulid,
      ));
      setMinted((current) => (
        current?.share_ulid === stale.share_ulid ? null : current
      ));
      const created = await requestMint();
      setMinted(created);
      setCopied(false);
      setReload((value) => value + 1);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not re-mint this link.");
    } finally {
      setReminting(false);
    }
  }

  const diverged = links.some((link) => link.diverged);
  const pending = minting || reminting || revoking !== null;

  return (
    <div
      className="scrim"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="dialog collection-share-dialog" role="dialog" aria-modal="true" aria-label="Make a link">
        <header className="dialog-head">
          <div className="dialog-meta">
            <h2 className="dialog-title">Make a link</h2>
            <p className="dialog-sub">{collectionName}</p>
          </div>
          <button className="dialog-close" onClick={onClose} ref={closeRef} aria-label="Close">
            <IconClose />
          </button>
        </header>

        <form
          className="collection-share-form"
          onSubmit={(event) => {
            event.preventDefault();
            void mintLink();
          }}
        >
          <fieldset className="collection-share-ttl">
            <legend>Link lifetime</legend>
            <div className="collection-share-choices" role="radiogroup" aria-label="Link lifetime">
              <button
                type="button"
                className={ttlHours === 168 ? "is-on" : ""}
                aria-pressed={ttlHours === 168}
                onClick={() => setTtlHours(168)}
              >
                7 days
              </button>
              <button
                type="button"
                className={ttlHours === 24 ? "is-on" : ""}
                aria-pressed={ttlHours === 24}
                onClick={() => setTtlHours(24)}
              >
                24 hours
              </button>
            </div>
          </fieldset>

          <label className="collection-share-note">
            <span>Liner note <span className="field-hint">optional</span></span>
            <textarea
              value={note}
              maxLength={280}
              rows={4}
              placeholder="A little context for the listener"
              onChange={(event) => setNote(event.target.value)}
            />
            <span className="collection-share-count num">{note.length}/280</span>
          </label>

          <button className="primary collection-share-mint" type="submit" disabled={pending}>
            {minting ? "Making link…" : "Make a link"}
          </button>
        </form>

        {minted ? (
          <div className="collection-share-created">
            <input className="public-share-input num" aria-label="New collection link" value={minted.url} readOnly />
            <button className="chip collection-share-copy" type="button" onClick={() => void copyLink()}>
              {copied ? <IconCheck /> : <IconLink />}
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
        ) : null}

        {error ? <p className="public-share-error" role="alert">{error}</p> : null}

        {diverged ? (
          <div className="collection-share-diverged">
            <p>This link plays the album as it was when you made it.</p>
            <button type="button" onClick={() => void remintLink()} disabled={pending}>
              {reminting ? "Re-minting…" : "Re-mint"}
            </button>
          </div>
        ) : null}

        {!loading && links.length ? (
          <section className="collection-share-list" aria-label="Active links" aria-live="polite">
            {links.map((link) => (
              <div className="collection-share-row" key={link.share_ulid}>
                <div>
                  <span className="num">{expiryCountdown(link.expires_at, now)}</span>
                  <span>{link.use_count} {link.use_count === 1 ? "use" : "uses"}</span>
                </div>
                <button
                  type="button"
                  onClick={() => requestRevoke(link.share_ulid)}
                  disabled={revoking === link.share_ulid}
                >
                  {revoking === link.share_ulid
                    ? "Revoking…"
                    : confirmRevoke === link.share_ulid ? "Really revoke?" : "Revoke"}
                </button>
              </div>
            ))}
          </section>
        ) : null}
      </div>
    </div>
  );
}
