"use client";

import {useEffect, useRef, useState} from "react";

import {Menu} from "./Menu";
import {type Member, useMembers} from "@/lib/members";

const WRITE = {"Content-Type": "application/json", "X-CR8-Request": "1"};
const DEFAULT_NOTE = "from my dig — the next 10 cold ones";

export function PassCrateDialog({
  bounceUlids,
  onClose,
  onPassed,
}: {
  bounceUlids: string[];
  onClose: () => void;
  onPassed: (member: Member) => void;
}) {
  const {members, loading: membersLoading, error: membersError} = useMembers();
  const [to, setTo] = useState("");
  const [note, setNote] = useState(DEFAULT_NOTE);
  const [state, setState] = useState<"idle" | "sending" | "error">("idle");
  const [message, setMessage] = useState("");
  const cancelRef = useRef<HTMLButtonElement | null>(null);
  const selectedMember = members.find((member) => member.username === to);

  useEffect(() => {
    if (members.length && !members.some((member) => member.username === to)) {
      setTo(members[0].username);
    }
  }, [members, to]);

  useEffect(() => {
    cancelRef.current?.focus();
  }, []);

  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && state !== "sending") onClose();
    };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [onClose, state]);

  async function passThemOn(event: React.FormEvent) {
    event.preventDefault();
    if (state === "sending" || !selectedMember) return;
    if (bounceUlids.length !== 10) {
      setState("error");
      setMessage("The next ten changed. Close this and try again.");
      return;
    }

    setState("sending");
    setMessage("");
    try {
      const response = await fetch("/api/assignments", {
        method: "POST",
        credentials: "same-origin",
        headers: WRITE,
        body: JSON.stringify({bounce_ulids: bounceUlids, to, note}),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail =
          payload && typeof payload === "object" && "detail" in payload
            ? String(payload.detail)
            : "Could not pass them on.";
        throw new Error(detail);
      }
      onPassed(selectedMember);
    } catch (problem) {
      setState("error");
      setMessage(
        problem instanceof Error ? problem.message : "Could not pass them on.",
      );
    }
  }

  const error = state === "error" ? message : membersError;

  return (
    <div
      className="scrim"
      onClick={(event) => {
        if (event.target === event.currentTarget && state !== "sending") onClose();
      }}
    >
      <div
        className="dialog pass-crate-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="pass-crate-title"
      >
        <h2 className="dialog-title" id="pass-crate-title">Pass the crate</h2>
        <p className="dialog-sub">The next 10 cold ones, with your reason riding along.</p>

        <form onSubmit={passThemOn} className="send-form">
          <label className="field">
            <span className="field-label">Who</span>
            <Menu
              label={
                selectedMember?.display ||
                selectedMember?.username ||
                (membersLoading ? "Loading people…" : "Choose someone")
              }
              options={members.map((member) => ({
                value: member.username,
                label: member.display || member.username,
              }))}
              value={to}
              onChange={setTo}
            />
          </label>
          <label className="field">
            <span className="field-label">
              Note <span className="field-hint">optional</span>
            </span>
            <input
              className="input"
              value={note}
              maxLength={280}
              onChange={(event) => setNote(event.target.value)}
            />
          </label>
          {error ? <p className="join-error" role="alert">{error}</p> : null}
          <div className="dialog-actions">
            <button
              className="btn btn-quiet"
              type="button"
              onClick={onClose}
              ref={cancelRef}
              disabled={state === "sending"}
            >
              Cancel
            </button>
            <button
              className="btn btn-main"
              disabled={state === "sending" || membersLoading || !selectedMember}
            >
              {state === "sending" ? "Passing…" : "Pass them on"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
