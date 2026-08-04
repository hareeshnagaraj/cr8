"use client";

import {useCallback, useEffect, useState} from "react";
import {usePlayer, type Track} from "@/components/PlayerProvider";

type Assignment = Track & {
  ulid: string;
  assigned_by: string;
  assigned_to: string;
  note: string;
  state: string;
  created_at: string;
  heard_at: string | null;
};

const WRITE = {"Content-Type": "application/json", "X-CR8-Request": "1"};

function ago(iso: string) {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const mins = Math.round((Date.now() - then) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

export default function ForYou() {
  const [items, setItems] = useState<Assignment[]>([]);
  const [sent, setSent] = useState<Assignment[]>([]);
  const [tab, setTab] = useState<"mine" | "sent">("mine");
  const [loaded, setLoaded] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const player = usePlayer();

  const load = useCallback(() => {
    fetch("/api/assignments", {credentials: "same-origin"})
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((data) => setItems(data.assignments ?? []))
      .catch(() => setItems([]))
      .finally(() => setLoaded(true));
    fetch("/api/assignments/sent", {credentials: "same-origin"})
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((data) => setSent(data.assignments ?? []))
      .catch(() => setSent([]));
  }, []);

  useEffect(load, [load]);

  // Listening marks things heard on the server; reflect that when the person
  // comes back to this tab rather than making them reload.
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") load();
    };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("pageshow", onVisible);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("pageshow", onVisible);
    };
  }, [load]);

  async function close(ulid: string, action: "done" | "dismiss") {
    const previousItems = items;
    setFeedback(null);
    setItems((current) => current.filter((item) => item.ulid !== ulid));
    const response = await fetch(`/api/assignments/${ulid}/${action}`, {
      method: "POST",
      credentials: "same-origin",
      headers: WRITE,
    }).catch(() => null);
    if (!response?.ok) {
      setItems(previousItems);
      setFeedback("Couldn’t update that assignment. Try again.");
      return;
    }
    load();
  }

  const list = tab === "mine" ? items : sent;

  return (
    <div className="lib page-scroll">
      <header className="lib-head">
        <div>
          <h1 className="lib-title">For You</h1>
          <p className="lib-count num">
            {items.length} to listen{items.length === 1 ? "" : ""}
          </p>
        </div>
        <div className="lib-actions">
          <button
            className={`btn${tab === "mine" ? " btn-main" : ""}`}
            onClick={() => setTab("mine")}
          >
            On your plate
          </button>
          <button
            className={`btn${tab === "sent" ? " btn-main" : ""}`}
            onClick={() => setTab("sent")}
          >
            You sent
          </button>
          {tab === "mine" && items.length > 1 ? (
            <button
              className="btn"
              onClick={() => player.play(items as Track[], 0)}
            >
              Play all
            </button>
          ) : null}
        </div>
      </header>

      {feedback ? (
        <p className="hw-feedback" role="alert">{feedback}</p>
      ) : null}

      {loaded && !list.length ? (
        <p className={`empty${tab === "mine" ? " teaching-empty" : ""}`}>
          {tab === "mine"
            ? "When someone sends you a track, it lands here with their note."
            : "You have not sent anyone a track yet. Use Send to in the inspector."}
        </p>
      ) : null}

      <ul className="hw-list">
        {list.map((item, position) => (
          <li
            key={item.ulid}
            className={`hw-card${item.state === "heard" ? " is-heard" : ""}`}
          >
            <img className="hw-art" src={`/art/${item.bounce_ulid}`} alt="" />
            <div className="hw-main">
              <button
                className="hw-title"
                onClick={() => player.play(list as Track[], position)}
              >
                {item.title}
              </button>
              <div className="hw-sub num">
                {tab === "mine" ? (
                  <span className="hw-from">from {item.assigned_by}</span>
                ) : (
                  <span className="hw-from">to {item.assigned_to}</span>
                )}
                {"  ·  "}
                {ago(item.created_at)}
                {item.state === "heard" ? "  ·  listened" : ""}
                {item.state === "done" ? "  ·  done" : ""}
              </div>
              {item.note ? <div className="hw-note">“{item.note}”</div> : null}
            </div>
            {tab === "mine" ? (
              <div className="hw-actions">
                <button className="btn" onClick={() => close(item.ulid, "done")}>
                  Done
                </button>
                <button
                  className="btn btn-quiet"
                  onClick={() => close(item.ulid, "dismiss")}
                >
                  Skip
                </button>
              </div>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}
