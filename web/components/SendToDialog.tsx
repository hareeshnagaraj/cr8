"use client";

import {useEffect, useRef, useState} from "react";
import {Menu} from "./Menu";
import {useMembers} from "@/lib/members";

const WRITE = {"Content-Type": "application/json", "X-CR8-Request": "1"};

export function SendToDialog({
  bounceUlid,
  title,
  onClose,
}: {
  bounceUlid: string;
  title: string;
  onClose: () => void;
}) {
  const {members, loading: membersLoading, error: membersError} = useMembers();
  const [to, setTo] = useState("");
  const [note, setNote] = useState("");
  const [state, setState] = useState<"idle" | "sending" | "sent" | "error">("idle");
  const [message, setMessage] = useState("");
  const closeTimer = useRef<number | null>(null);
  const liveRef = useRef(true);
  const selectedMember = members.find((member) => member.username === to);

  useEffect(() => {
    if (members.length && !members.some((member) => member.username === to)) {
      setTo(members[0].username);
    }
  }, [members, to]);

  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [onClose]);

  useEffect(() => {
    liveRef.current = true;
    return () => {
      liveRef.current = false;
      if (closeTimer.current !== null) window.clearTimeout(closeTimer.current);
    };
  }, []);

  async function send(event: React.FormEvent) {
    event.preventDefault();
    if (state === "sending" || !to) return;
    setState("sending");
    try {
      const response = await fetch("/api/assignments", {
        method: "POST",
        credentials: "same-origin",
        headers: WRITE,
        body: JSON.stringify({bounce_ulids: [bounceUlid], to, note}),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail ?? "could not send it");
      if (!liveRef.current) return;
      setState("sent");
      setMessage(
        data.created
          ? `On ${to}'s plate.`
          : `${to} already has this one.`,
      );
      if (closeTimer.current !== null) window.clearTimeout(closeTimer.current);
      closeTimer.current = window.setTimeout(onClose, 1100);
    } catch (problem) {
      if (!liveRef.current) return;
      setState("error");
      setMessage(problem instanceof Error ? problem.message : "something went wrong");
    }
  }

  return (
    <div className="scrim" onClick={onClose}>
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-label={`Send ${title} to someone`}
        onClick={(event) => event.stopPropagation()}
      >
        <h2 className="dialog-title">Send to</h2>
        <p className="dialog-sub">{title}</p>

        {state === "sent" ? (
          <p className="dialog-note">{message}</p>
        ) : (
          <form onSubmit={send} className="send-form">
            <label className="field">
              <span className="field-label">Who</span>
              <Menu
                label={
                  selectedMember?.display ||
                  selectedMember?.username ||
                  "Choose someone"
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
                placeholder="the bridge at 1:20"
                onChange={(event) => setNote(event.target.value)}
              />
            </label>
            {state === "error" || membersError ? (
              <p className="join-error" role="alert">{message || membersError}</p>
            ) : null}
            <div className="dialog-actions">
              <button className="btn btn-quiet" type="button" onClick={onClose}>
                Cancel
              </button>
              <button
                className="btn btn-main"
                disabled={state === "sending" || membersLoading || !members.length}
              >
                {state === "sending" ? "Sending…" : "Put it on their plate"}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
