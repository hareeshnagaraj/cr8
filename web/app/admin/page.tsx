"use client";

import {useCallback, useEffect, useMemo, useState} from "react";
import {Menu, type MenuOption} from "@/components/Menu";

type Invite = {
  ulid: string;
  label: string;
  role: string;
  created_by: string;
  created_at: string;
  expires_at: string | null;
  max_uses: number | null;
  use_count: number;
  revoked_at: string | null;
  claimed_by: string | null;
  bounce_ulid: string | null;
  song_title: string | null;
  state: string;
  usable: boolean;
};

type Me = {username: string; display: string; role: string; is_admin: boolean};

type Member = {
  id: number;
  username: string;
  display: string;
  role: string;
  is_you: boolean;
};

type InviteTrack = {
  bounce_ulid: string;
  title: string;
  version_label?: string | null;
};

const WRITE = {"Content-Type": "application/json", "X-CR8-Request": "1"};
const ROLE_OPTIONS: MenuOption[] = [
  {value: "band", label: "Member"},
  {value: "owner", label: "Admin"},
];

function when(value: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleDateString(undefined, {month: "short", day: "numeric"});
}

export default function Admin() {
  const [me, setMe] = useState<Me | null>(null);
  const [invites, setInvites] = useState<Invite[]>([]);
  const [members, setMembers] = useState<Member[]>([]);
  const [tracks, setTracks] = useState<InviteTrack[]>([]);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [fresh, setFresh] = useState<{url: string; label: string} | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [label, setLabel] = useState("");
  const [role, setRole] = useState("band");
  const [uses, setUses] = useState("1");
  const [days, setDays] = useState("14");
  const [bounceUlid, setBounceUlid] = useState("");

  const trackOptions = useMemo<MenuOption[]>(
    () => [
      {value: "", label: "No song — open the library"},
      ...tracks.map((track) => ({
        value: track.bounce_ulid,
        label: track.version_label
          ? `${track.title} · ${track.version_label}`
          : track.title,
      })),
    ],
    [tracks],
  );
  const selectedTrack = tracks.find((track) => track.bounce_ulid === bounceUlid);

  const load = useCallback(() => {
    fetch("/api/admin/invites", {credentials: "same-origin"})
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((data) => setInvites(data.invites ?? []))
      .catch(() => setInvites([]));
    fetch("/api/admin/members", {credentials: "same-origin"})
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((data) => setMembers(data.members ?? []))
      .catch(() => setMembers([]));
  }, []);

  async function removeMember(username: string) {
    setError(null);
    const response = await fetch(`/api/admin/members/${username}/remove`, {
      method: "POST",
      credentials: "same-origin",
      headers: WRITE,
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      setError(data.detail ?? "could not remove that member");
    }
    setConfirming(null);
    load();
  }

  useEffect(() => {
    fetch("/api/me", {credentials: "same-origin"})
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then(setMe)
      .catch(() => setMe(null));
    fetch("/api/library?limit=1000&sort=title", {credentials: "same-origin"})
      .then((response) => (
        response.ok ? response.json() : Promise.reject(response.status)
      ))
      .then((data: {tracks?: InviteTrack[]}) => {
        setTracks(Array.isArray(data.tracks) ? data.tracks : []);
      })
      .catch(() => setTracks([]));
    load();
  }, [load]);

  async function create(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return; // a second click must not mint a second invite
    setBusy(true);
    setError(null);
    try {
      const response = await fetch("/api/admin/invites", {
        method: "POST",
        credentials: "same-origin",
        headers: WRITE,
        body: JSON.stringify({
          label,
          role,
          max_uses: uses === "" ? null : Number(uses),
          expires_days: days === "" ? null : Number(days),
          bounce_ulid: bounceUlid || null,
        }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail ?? "could not create invite");
      setFresh({url: data.join_url, label: label || "invite"});
      setCopied(false);
      setLabel("");
      setBounceUlid("");
      load();
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : "something went wrong");
    } finally {
      setBusy(false);
    }
  }

  async function revoke(ulid: string) {
    setError(null);
    const response = await fetch(`/api/admin/invites/${ulid}/revoke`, {
      method: "POST",
      credentials: "same-origin",
      headers: WRITE,
    });
    if (!response.ok) {
      setError("could not revoke that invite");
      return;
    }
    load();
  }

  async function copy(url: string) {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  if (me && !me.is_admin) {
    return (
      <div className="lib page-scroll">
        <header className="lib-head">
          <h1 className="lib-title">Admin</h1>
        </header>
        <p className="empty">This page is for admins.</p>
      </div>
    );
  }

  return (
    <div className="lib page-scroll">
      <header className="lib-head">
        <div>
          <h1 className="lib-title">Admin</h1>
          <p className="lib-count num">
            {invites.filter((i) => i.usable).length} live ·{" "}
            {invites.length} total
          </p>
        </div>
      </header>

      {error ? <p className="admin-error">{error}</p> : null}

      {fresh ? (
        <div className="invite-fresh">
          <div className="invite-fresh-head">
            Link for {fresh.label}. It is shown once.
          </div>
          <div className="invite-fresh-row">
            <code className="invite-url">{fresh.url}</code>
            <button className="btn" onClick={() => copy(fresh.url)}>
              {copied ? "Copied" : "Copy"}
            </button>
            <button className="btn btn-quiet" onClick={() => setFresh(null)}>
              Done
            </button>
          </div>
        </div>
      ) : null}

      <form className="invite-form" onSubmit={create}>
        <label className="field">
          <span className="field-label">Who is it for</span>
          <input
            className="input"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="connor"
          />
        </label>
        <label className="field invite-song-field">
          <span className="field-label">Start them on a song</span>
          <Menu
            label={selectedTrack?.title ?? "No song — open the library"}
            options={trackOptions}
            value={bounceUlid}
            onChange={setBounceUlid}
            searchable
          />
        </label>
        <label className="field">
          <span className="field-label">Access</span>
          <Menu
            label={role === "owner" ? "Admin" : "Member"}
            options={ROLE_OPTIONS}
            value={role}
            onChange={setRole}
          />
        </label>
        <label className="field field-narrow">
          <span className="field-label">Uses</span>
          <input
            className="input num"
            type="number"
            inputMode="numeric"
            min="1"
            value={uses}
            onChange={(e) => setUses(e.target.value)}
          />
        </label>
        <label className="field field-narrow">
          <span className="field-label">Days</span>
          <input
            className="input num"
            type="number"
            inputMode="numeric"
            min="1"
            value={days}
            onChange={(e) => setDays(e.target.value)}
          />
        </label>
        <button className="btn btn-main" disabled={busy}>
          {busy ? "Making…" : "Make a link"}
        </button>
      </form>

      <section className="admin-section">
        <h2 className="admin-sub">Who is in</h2>
        <ul className="invite-list">
          {members.map((member) => (
            <li key={member.username} className="invite-row">
              <div className="invite-main">
                <span className="invite-label">{member.display}</span>
                <span className="invite-meta num">@{member.username}</span>
                {member.role === "owner" ? (
                  <span className="pill pill-admin">admin</span>
                ) : (
                  <span className="pill">member</span>
                )}
              </div>
              {member.is_you ? (
                <span className="invite-meta">that is you</span>
              ) : confirming === member.username ? (
                <div className="hw-actions">
                  <button
                    className="btn"
                    onClick={() => removeMember(member.username)}
                  >
                    Really remove
                  </button>
                  <button
                    className="btn btn-quiet"
                    onClick={() => setConfirming(null)}
                  >
                    Keep
                  </button>
                </div>
              ) : (
                <button
                  className="btn btn-quiet"
                  onClick={() => setConfirming(member.username)}
                >
                  Remove
                </button>
              )}
            </li>
          ))}
        </ul>
      </section>

      <h2 className="admin-sub">Invites</h2>

      {!invites.length ? (
        <p className="empty">
          No invites yet. A link lets one person set their own password and land
          straight in the library.
        </p>
      ) : (
        <ul className="invite-list">
          {invites.map((invite) => (
            <li key={invite.ulid} className="invite-row">
              <div className="invite-main">
                <span className="invite-label">
                  {invite.label || "unlabelled"}
                </span>
                <span className={`pill pill-${invite.state}`}>
                  {invite.state}
                </span>
                {invite.role === "owner" ? (
                  <span className="pill pill-admin">admin</span>
                ) : null}
              </div>
              <div className="invite-meta num">
                {invite.claimed_by ? `claimed by ${invite.claimed_by}` : null}
                {invite.claimed_by ? " · " : ""}
                {invite.song_title ? `${invite.song_title} · ` : ""}
                {invite.use_count}
                {invite.max_uses ? `/${invite.max_uses}` : ""} used · expires{" "}
                {when(invite.expires_at)}
              </div>
              {invite.usable ? (
                <button
                  className="btn btn-quiet"
                  onClick={() => revoke(invite.ulid)}
                >
                  Revoke
                </button>
              ) : (
                <span className="invite-spent" />
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
