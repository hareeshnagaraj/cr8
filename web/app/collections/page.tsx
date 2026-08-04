"use client";

import {
  useEffect, useMemo, useRef, useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import {CollectionShareDialog} from "@/components/CollectionShareDialog";
import {usePlayer, type Track} from "@/components/PlayerProvider";

type Collection = {
  ulid: string;
  name: string;
  song_count?: number;
  track_count?: number;
};

type DragState = {
  pointerId: number;
  from: number;
  to: number;
};

function reorder(tracks: Track[], from: number, to: number) {
  if (from === to) return tracks;
  const ordered = tracks.slice();
  const [track] = ordered.splice(from, 1);
  ordered.splice(Math.max(0, Math.min(to, ordered.length)), 0, track);
  return ordered;
}

export function CollectionsView({initialUlid = null}: {initialUlid?: string | null}) {
  const [collections, setCollections] = useState<Collection[] | null>(null);
  const [listError, setListError] = useState(false);
  const [openUlid, setOpenUlid] = useState<string | null>(initialUlid);
  const [tracks, setTracks] = useState<Track[]>([]);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [mutationPending, setMutationPending] = useState(false);
  const [drag, setDrag] = useState<DragState | null>(null);
  const dragRef = useRef<DragState | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [sharing, setSharing] = useState(false);
  const confirmTimer = useRef<number | null>(null);
  const player = usePlayer();

  useEffect(() => {
    let cancelled = false;
    fetch("/api/collections", {credentials: "same-origin"})
      .then((response) => (response.ok ? response.json() : Promise.reject(response.status)))
      .then((data: Collection[]) => {
        if (!cancelled) setCollections(data);
      })
      .catch(() => {
        if (cancelled) return;
        setCollections([]);
        setListError(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    setTracks([]);
    setDetailError(null);
    setConfirmDelete(false);
    setSharing(false);
    if (confirmTimer.current !== null) window.clearTimeout(confirmTimer.current);
    if (!openUlid) {
      setDetailLoading(false);
      return;
    }
    let cancelled = false;
    setDetailLoading(true);
    fetch(`/api/collections/${openUlid}`, {credentials: "same-origin"})
      .then((response) => (response.ok ? response.json() : Promise.reject(response.status)))
      .then((data: {tracks?: Track[]}) => {
        if (!cancelled) setTracks(data.tracks ?? []);
      })
      .catch(() => {
        if (!cancelled) setDetailError("Couldn’t load this collection.");
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [openUlid]);

  useEffect(() => () => {
    if (confirmTimer.current !== null) window.clearTimeout(confirmTimer.current);
  }, []);

  const orderedTracks = useMemo(
    () => (drag ? reorder(tracks, drag.from, drag.to) : tracks),
    [drag, tracks],
  );
  const playingUlid = player.current?.bounce_ulid;
  const openCollection = collections?.find((collection) => collection.ulid === openUlid);

  function updateCount(delta: number) {
    if (!openUlid) return;
    setCollections((current) => current?.map((collection) => {
      if (collection.ulid !== openUlid) return collection;
      const count = Math.max(0, (collection.track_count ?? collection.song_count ?? 0) + delta);
      return {...collection, track_count: count};
    }) ?? null);
  }

  function startDrag(event: ReactPointerEvent<HTMLButtonElement>, index: number) {
    if (event.button !== 0 || mutationPending) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    document.getSelection()?.removeAllRanges();
    const next = {pointerId: event.pointerId, from: index, to: index};
    dragRef.current = next;
    setDrag(next);
  }

  function updateDrag(event: ReactPointerEvent<HTMLButtonElement>) {
    const active = dragRef.current;
    if (!active || active.pointerId !== event.pointerId) return;
    event.preventDefault();
    const rows = Array.from(
      listRef.current?.querySelectorAll<HTMLElement>("[data-collection-position]") ?? [],
    );
    const before = rows.find(
      (row) => Number(row.dataset.collectionPosition) === active.to - 1,
    );
    const after = rows.find(
      (row) => Number(row.dataset.collectionPosition) === active.to + 1,
    );
    let to = active.to;
    if (before) {
      const bounds = before.getBoundingClientRect();
      if (event.clientY < bounds.top + bounds.height / 2) to -= 1;
    }
    if (to === active.to && after) {
      const bounds = after.getBoundingClientRect();
      if (event.clientY > bounds.top + bounds.height / 2) to += 1;
    }
    if (to === active.to) return;
    const next = {...active, to};
    dragRef.current = next;
    setDrag(next);
  }

  async function persistOrder(before: Track[], after: Track[]) {
    if (!openUlid) return;
    setTracks(after);
    setMutationPending(true);
    setDetailError(null);
    const body = new URLSearchParams();
    after.forEach((track) => body.append("bounce_ulid", track.bounce_ulid));
    try {
      const response = await fetch(`/api/collections/${openUlid}/order`, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
          "X-CR8-Request": "1",
        },
        body,
      });
      if (!response.ok) throw new Error(`collection reorder failed: ${response.status}`);
    } catch {
      setTracks(before);
      setDetailError("Couldn’t save that order. The previous order is restored.");
    } finally {
      setMutationPending(false);
    }
  }

  function finishDrag(event: ReactPointerEvent<HTMLButtonElement>) {
    const active = dragRef.current;
    if (!active || active.pointerId !== event.pointerId) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    dragRef.current = null;
    setDrag(null);
    if (active.from !== active.to) {
      void persistOrder(tracks, reorder(tracks, active.from, active.to));
    }
  }

  function cancelDrag(event: ReactPointerEvent<HTMLButtonElement>) {
    if (dragRef.current?.pointerId !== event.pointerId) return;
    dragRef.current = null;
    setDrag(null);
  }

  async function removeTrack(track: Track) {
    if (!openUlid || mutationPending) return;
    const before = tracks;
    setTracks((current) => current.filter((item) => item.bounce_ulid !== track.bounce_ulid));
    setMutationPending(true);
    setDetailError(null);
    try {
      const response = await fetch(
        `/api/collections/${openUlid}/remove/${track.bounce_ulid}`,
        {
          method: "POST",
          credentials: "same-origin",
          headers: {"X-CR8-Request": "1"},
        },
      );
      if (!response.ok) throw new Error(`collection remove failed: ${response.status}`);
      updateCount(-1);
    } catch {
      setTracks(before);
      setDetailError("Couldn’t remove that song. It has been restored.");
    } finally {
      setMutationPending(false);
    }
  }

  function armDelete() {
    if (confirmDelete) {
      void deleteOpenCollection();
      return;
    }
    setConfirmDelete(true);
    if (confirmTimer.current !== null) window.clearTimeout(confirmTimer.current);
    confirmTimer.current = window.setTimeout(() => setConfirmDelete(false), 3_000);
  }

  async function deleteOpenCollection() {
    if (!openUlid || mutationPending) return;
    setMutationPending(true);
    setDetailError(null);
    try {
      const response = await fetch(`/api/collections/${openUlid}/delete`, {
        method: "POST",
        credentials: "same-origin",
        headers: {"X-CR8-Request": "1"},
      });
      if (!response.ok) throw new Error(`collection delete failed: ${response.status}`);
      setCollections((current) => current?.filter(
        (collection) => collection.ulid !== openUlid,
      ) ?? null);
      setOpenUlid(null);
    } catch {
      setDetailError("Couldn’t delete this collection.");
    } finally {
      setMutationPending(false);
      setConfirmDelete(false);
    }
  }

  return (
    <div className="lib page-scroll">
      <header className="lib-head">
        <div>
          <h1 className="lib-title">Collections</h1>
          <p className="lib-count num">
            {collections === null ? "Loading" : `${collections.length} collections`}
          </p>
        </div>
      </header>

      {listError ? (
        <p className="empty" role="alert">Couldn’t load collections.</p>
      ) : null}
      {collections !== null && !listError && !collections.length ? (
        <p className="empty">
          No collections yet. Select songs in the library and hit New collection.
        </p>
      ) : null}

      <div className="cards">
        {collections?.map((collection) => {
          const count = collection.track_count ?? collection.song_count ?? 0;
          const isOpen = openUlid === collection.ulid;
          return (
            <button
              key={collection.ulid}
              className={`card${isOpen ? " is-open" : ""}`}
              aria-expanded={isOpen}
              disabled={mutationPending}
              onClick={() => setOpenUlid(isOpen ? null : collection.ulid)}
            >
              <span className="card-name">{collection.name}</span>
              <span className="card-sub num">{count} songs</span>
            </button>
          );
        })}
      </div>

      {openUlid ? (
        <section className="collection-detail" aria-label={openCollection?.name ?? "Collection"}>
          <div className="collection-actions">
            <button
              className="primary"
              disabled={!tracks.length || detailLoading || mutationPending}
              onClick={() => player.play(tracks, 0)}
            >
              Play collection
            </button>
            <button
              className="collection-share-action"
              disabled={!tracks.length || detailLoading || mutationPending}
              onClick={() => setSharing(true)}
            >
              Make a link
            </button>
            <button
              className={`collection-delete${confirmDelete ? " is-confirming" : ""}`}
              disabled={detailLoading || mutationPending}
              onClick={armDelete}
            >
              {confirmDelete ? "Really delete?" : "Delete"}
            </button>
          </div>
          {detailError ? <p className="collection-error" role="alert">{detailError}</p> : null}
          {detailLoading ? <p className="empty">Loading collection…</p> : null}
          {!detailLoading && !detailError && !tracks.length ? (
            <p className="empty">This collection has no songs.</p>
          ) : null}
          {!detailLoading && tracks.length ? (
            <div className="collection-list" ref={listRef}>
              {orderedTracks.map((track, index) => (
                <div
                  key={track.bounce_ulid}
                  className={`collection-row${
                    track.bounce_ulid === playingUlid ? " is-playing" : ""
                  }${
                    drag && tracks[drag.from]?.bounce_ulid === track.bounce_ulid
                      ? " is-dragging"
                      : ""
                  }`}
                  data-collection-position={index}
                >
                  <button
                    className="collection-handle"
                    aria-label={`Reorder ${track.title}`}
                    disabled={mutationPending}
                    onPointerDown={(event) => startDrag(event, index)}
                    onPointerMove={updateDrag}
                    onPointerUp={finishDrag}
                    onPointerCancel={cancelDrag}
                  >
                    ≡
                  </button>
                  <button
                    className="collection-track"
                    onClick={() => player.play(orderedTracks, index)}
                  >
                    <span className="collection-position num">{index + 1}</span>
                    <span className="collection-track-name">{track.title}</span>
                    <span className="collection-track-meta num">
                      {track.key_canon || "—"} · {track.duration_label || "—"}
                    </span>
                  </button>
                  <button
                    className="collection-remove"
                    aria-label={`Remove ${track.title} from collection`}
                    disabled={mutationPending}
                    onClick={() => void removeTrack(track)}
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
          ) : null}
        </section>
      ) : null}
      {sharing && openUlid && openCollection ? (
        <CollectionShareDialog
          collectionUlid={openUlid}
          collectionName={openCollection.name}
          onClose={() => setSharing(false)}
        />
      ) : null}
    </div>
  );
}

export default function Collections() {
  return <CollectionsView />;
}
