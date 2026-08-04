"use client";

import {useState, type FormEvent} from "react";
import {TagInput} from "@/components/TagInput";
import type {Track} from "@/components/PlayerProvider";
import type {BulkFeedback} from "@/hooks/useHearts";
import {releaseFocus} from "@/lib/focus";

export function BulkBar({
  chosen,
  onDone,
  onConsumed,
  onPlay,
  onEnqueue,
  setFeedback,
}: {
  chosen: Track[];
  onDone: () => void;
  onConsumed: () => void;
  onPlay: (tracks: Track[]) => void;
  onEnqueue: (tracks: Track[]) => void;
  setFeedback: (feedback: BulkFeedback) => void;
}) {
  const [collectionMakerOpen, setCollectionMakerOpen] = useState(false);
  const [collectionName, setCollectionName] = useState("");
  const [collectionPending, setCollectionPending] = useState(false);
  const [tagInputOpen, setTagInputOpen] = useState(false);

  const first = chosen[0];
  const pickedUlids = chosen.map((track) => track.bounce_ulid);

  // Bulk tagging reuses the single-song write once per selected song; the server
  // owns provenance and undo either way, so there is no second code path.
  async function tagPicked(dim: string, value: string, keepSelection = false) {
    const responses = await Promise.all(
      chosen.map((track) =>
        fetch(`/api/songs/${track.song_ulid}/tags/toggle`, {
          method: "POST",
          credentials: "same-origin",
          headers: {"Content-Type": "application/json", "X-CR8-Request": "1"},
          body: JSON.stringify({dim, value, bounce_ulid: track.bounce_ulid}),
        }).catch(() => null),
      ),
    );
    const tagged = responses.filter((response) => response?.ok).length;
    const failed = chosen.length - tagged;
    setFeedback({
      kind: failed ? "error" : "success",
      message: failed
        ? `${tagged} tagged, ${failed} failed — retry`
        : `tagged ${tagged}`,
    });
    if (!failed && !keepSelection) onConsumed();
    return failed === 0;
  }

  async function createCollectionFromSelection(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const name = collectionName.trim();
    if (!name || !chosen.length || collectionPending) return;

    const body = new URLSearchParams({name, source: "selection"});
    pickedUlids.forEach((bounceUlid) => body.append("bounce_ulid", bounceUlid));
    setCollectionPending(true);
    setFeedback(null);
    try {
      const response = await fetch("/api/collections", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
          "X-CR8-Request": "1",
        },
        body,
      });
      if (!response.ok) throw new Error(`collection create failed: ${response.status}`);
      setFeedback({kind: "success", message: `Created “${name}”.`});
      setCollectionName("");
      setCollectionMakerOpen(false);
      releaseFocus();
    } catch {
      setFeedback({
        kind: "error",
        message: "Couldn’t create that collection. Try again.",
      });
    } finally {
      setCollectionPending(false);
    }
  }

  function closeCollectionMaker() {
    setCollectionMakerOpen(false);
    setCollectionName("");
    releaseFocus();
  }

  function closeTagInput() {
    setTagInputOpen(false);
    releaseFocus();
  }

  return (
    <div className="bulkbar">
      <span className="num">{chosen.length} selected</span>
      {tagInputOpen && first ? (
        <TagInput
          className="bulk-tag-input"
          sourceSongUlid={first.song_ulid}
          placeholder="Tag…"
          ariaLabel="Tag selected songs"
          autoFocus
          onEscape={closeTagInput}
          onApply={(dim, value) => tagPicked(dim, value, true)}
        />
      ) : (
        <button
          className="chip"
          onClick={() => {
            setFeedback(null);
            setTagInputOpen(true);
          }}
        >
          Tag…
        </button>
      )}
      <button className="chip" onClick={() => tagPicked("status", "demo")}>
        Mark demo
      </button>
      <button
        className="chip"
        onClick={() => {
          if (chosen.length) onPlay(chosen);
        }}
      >
        Play these
      </button>
      <button
        className="chip"
        onClick={() => onEnqueue(chosen)}
      >
        Queue these
      </button>
      <a
        className="chip"
        href={`/download/selection?ulids=${encodeURIComponent(pickedUlids.join(","))}`}
      >
        Download zip
      </a>
      {collectionMakerOpen ? (
        <form className="collection-maker" onSubmit={createCollectionFromSelection}>
          <input
            className="input collection-name-input"
            value={collectionName}
            onChange={(event) => setCollectionName(event.target.value)}
            onKeyDown={(event) => {
              if (event.key !== "Escape") return;
              event.preventDefault();
              closeCollectionMaker();
            }}
            placeholder="Collection name"
            aria-label="Collection name"
            maxLength={80}
            autoFocus
            disabled={collectionPending}
          />
          <button
            className="chip"
            type="submit"
            disabled={!collectionName.trim() || collectionPending}
          >
            {collectionPending ? "Creating…" : "Create"}
          </button>
          <button
            className="chip"
            type="button"
            disabled={collectionPending}
            onClick={closeCollectionMaker}
          >
            Cancel
          </button>
        </form>
      ) : (
        <button
          className="chip"
          onClick={() => {
            setFeedback(null);
            setCollectionMakerOpen(true);
          }}
        >
          New collection
        </button>
      )}
      <button
        className="chip"
        onClick={onDone}
      >
        Clear
      </button>
    </div>
  );
}
