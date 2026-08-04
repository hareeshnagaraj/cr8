"use client";

import {useCallback, useEffect, useRef, useState} from "react";
import {usePlayer, type Track} from "@/components/PlayerProvider";
import dynamic from "next/dynamic";
import {isVideoFile} from "@/lib/videoFile";

// The video extract stack (mediabunny + WebCodecs plumbing) is ~half a
// megabyte and belongs only to the moment someone actually drops a video —
// the payload budget in perf-budgets.json is why this is dynamic.
const VideoTrim = dynamic(
  () => import("@/components/VideoTrim").then((m) => m.VideoTrim),
  {ssr: false},
);

type Upload = {
  ulid: string;
  filename: string;
  size_bytes: number;
  uploaded_by: string;
  source: string;
  created_at: string;
  state: string;
  detail: string | null;
  title: string | null;
  song_ulid: string | null;
  bounce_ulid: string | null;
};

type Sending = {
  id: number;
  name: string;
  size: number;
  progress: number;
  state: "sending" | "done" | "error" | "duplicate";
  fromVideo: boolean;
  error?: string;
};

type UploadCandidate = {file: File; fromVideo: boolean};
type QueuedVideo = {id: number; file: File};

let nextUploadId = 0;
let nextVideoId = 0;

const STATE_LABEL: Record<string, string> = {
  pending: "waiting for the pipeline",
  ingesting: "being catalogued",
  ingested: "in the crate",
  needs_review: "needs a name",
  rejected: "rejected",
};

function size(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export default function UploadPage() {
  const [recent, setRecent] = useState<Upload[]>([]);
  const [sending, setSending] = useState<Sending[]>([]);
  const [videoQueue, setVideoQueue] = useState<QueuedVideo[]>([]);
  const [over, setOver] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const pollingStartedAt = useRef<number | null>(null);
  const player = usePlayer();

  const load = useCallback(() => {
    fetch("/api/uploads", {credentials: "same-origin"})
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((data) => setRecent(data.uploads ?? []))
      .catch(() => setRecent([]));
  }, []);

  useEffect(load, [load]);

  // Ingestion happens out of band, so the list has to come back and look again
  // rather than assuming the state it had when the upload finished.
  useEffect(() => {
    const waiting = recent.some(
      (item) => item.state === "pending" || item.state === "ingesting",
    );
    if (!waiting) {
      pollingStartedAt.current = null;
      return;
    }
    if (pollingStartedAt.current === null) pollingStartedAt.current = Date.now();

    let timer: ReturnType<typeof setTimeout> | undefined;
    const stopTimer = () => {
      if (timer === undefined) return;
      clearTimeout(timer);
      timer = undefined;
    };
    const schedule = () => {
      if (document.visibilityState === "hidden") return;
      const elapsed = Date.now() - (pollingStartedAt.current ?? Date.now());
      timer = setTimeout(load, elapsed < 2 * 60 * 1000 ? 5000 : 15000);
    };
    const visibilityChanged = () => {
      stopTimer();
      if (document.visibilityState === "visible") load();
    };

    schedule();
    document.addEventListener("visibilitychange", visibilityChanged);
    return () => {
      stopTimer();
      document.removeEventListener("visibilitychange", visibilityChanged);
    };
  }, [recent, load]);

  // XHR rather than fetch: it reports upload progress, and a 300 MB bounce over
  // a home connection needs a progress bar to not look frozen.
  const send = useCallback(
    (candidates: UploadCandidate[]) => {
      if (!candidates.length) return;
      const started: Sending[] = candidates.map(({file, fromVideo}) => ({
        id: ++nextUploadId,
        name: file.name,
        size: file.size,
        progress: 0,
        state: "sending",
        fromVideo,
      }));
      setSending((current) => [...started, ...current]);

      candidates.forEach(({file}, offset) => {
        const entryId = started[offset].id;
        // Cloudflare's edge refuses request bodies over 100 MB before they
        // reach the app (measured: a 110 MB POST gets an HTML 413 from the
        // edge). Fail here instead of after minutes of upload.
        if (file.size > 100 * 1024 * 1024) {
          setSending((current) =>
            current.map((entry) =>
              entry.id === entryId
                ? {...entry, state: "error", error: "too big — 100 MB max over the tunnel"}
                : entry,
            ),
          );
          return;
        }
        const body = new FormData();
        body.append("file", file);
        const request = new XMLHttpRequest();
        request.open("POST", "/api/upload");
        request.setRequestHeader("X-CR8-Request", "1");
        request.withCredentials = true;

        const update = (patch: Partial<Sending>) =>
          setSending((current) =>
            current.map((entry) =>
              entry.id === entryId
                ? {...entry, ...patch}
                : entry,
            ),
          );

        request.upload.onprogress = (event) => {
          if (event.lengthComputable) {
            update({progress: event.loaded / event.total});
          }
        };
        request.onload = () => {
          let payload: {
            files?: {
              ok: boolean;
              filename?: string;
              error?: string;
              duplicate_of?: string;
            }[];
          } = {};
          try {
            payload = JSON.parse(request.responseText);
          } catch {
            payload = {};
          }
          const result = payload.files?.[0];
          if (request.status >= 400 || (result && !result.ok)) {
            let error = result?.error;
            if (!error) {
              if (request.status === 401) {
                error = "signed out — log in again";
              } else if (request.status === 413) {
                error = "too big — 100 MB max over the tunnel";
              } else if (request.status === 429) {
                error = "too many uploads at once — give it a minute";
              } else {
                error = `upload failed — status ${request.status}`;
              }
            }
            update({
              state: "error",
              error,
            });
            if (request.status === 401) {
              window.location.href = "/login?next=/upload";
            }
          } else if (result?.duplicate_of) {
            update({
              name: result.filename ?? file.name,
              state: "duplicate",
              progress: 1,
            });
          } else {
            update({name: result?.filename ?? file.name, state: "done", progress: 1});
          }
          load();
        };
        request.onerror = () =>
          update({state: "error", error: "the connection dropped"});
        request.send(body);
      });
    },
    [load],
  );

  const chooseFiles = useCallback(
    (files: File[]) => {
      const video: File[] = [];
      const audio: UploadCandidate[] = [];
      for (const file of files) {
        if (isVideoFile(file)) video.push(file);
        else audio.push({file, fromVideo: false});
      }
      send(audio);
      if (video.length) {
        setVideoQueue((current) => [
          ...current,
          ...video.map((file) => ({id: ++nextVideoId, file})),
        ]);
      }
    },
    [send],
  );

  const finishVideo = useCallback(() => {
    setVideoQueue((current) => current.slice(1));
  }, []);

  return (
    <div className="lib page-scroll">
      <header className="lib-head">
        <div>
          <h1 className="lib-title">Upload</h1>
          <p className="lib-count num">
            Lands in the crate and goes through the normal pipeline
          </p>
        </div>
      </header>

      <div
        className={`drop${over ? " is-over" : ""}`}
        onDragOver={(event) => {
          event.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(event) => {
          event.preventDefault();
          setOver(false);
          chooseFiles(Array.from(event.dataTransfer.files));
        }}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") inputRef.current?.click();
        }}
      >
        <div className="drop-main">Drop audio or video here</div>
        <div className="drop-sub">
          wav, aiff, flac, mp3, m4a, mp4, mov, webm or mkv · up to 100 MB each
        </div>
        <input
          ref={inputRef}
          hidden
          type="file"
          multiple
          accept=".wav,.aif,.aiff,.flac,.mp3,.m4a,.mp4,.mov,.webm,.mkv"
          onChange={(event) => {
            chooseFiles(Array.from(event.target.files ?? []));
            event.target.value = "";
          }}
        />
      </div>

      {videoQueue[0] ? (
        <VideoTrim
          key={videoQueue[0].id}
          file={videoQueue[0].file}
          onClose={finishVideo}
          onExtracted={(file) => {
            send([{file, fromVideo: true}]);
            finishVideo();
          }}
          onServerFallback={(file) => {
            send([{file, fromVideo: true}]);
            finishVideo();
          }}
        />
      ) : null}

      {sending.length ? (
        <ul className="send-list">
          {sending.map((item) => (
            <li key={item.id} className="send-row">
              <div className="send-main">
                <span className="send-name-wrap">
                  <span className="send-name">{item.name}</span>
                  {item.fromVideo ? <span className="send-origin">from video</span> : null}
                </span>
                <span className="send-meta num">
                  {item.state === "error"
                    ? item.error
                    : item.state === "duplicate"
                      ? "already in the crate"
                      : item.state === "done"
                        ? "sent"
                        : `${Math.round(item.progress * 100)}%`}
                </span>
              </div>
              <div className="send-track">
                <i
                  className={`send-fill is-${item.state}`}
                  style={{width: `${Math.max(2, item.progress * 100)}%`}}
                />
              </div>
            </li>
          ))}
        </ul>
      ) : null}

      <h2 className="admin-sub">Recently added</h2>
      {!recent.length ? (
        <p className="empty">
          Nothing uploaded yet. Anything dropped here is catalogued the same way
          files on the machine are.
        </p>
      ) : (
        <ul className="invite-list">
          {recent.map((item) => {
            // Once the pipeline has caught up there is a real track behind this
            // row, and seeing something in a list you cannot open is maddening.
            const playable = recent.filter((entry) => entry.bounce_ulid);
            const position = playable.findIndex((entry) => entry.ulid === item.ulid);
            return (
              <li
                key={item.ulid}
                className={`invite-row${item.bounce_ulid ? " is-playable" : ""}`}
                onClick={
                  item.bounce_ulid
                    ? () => player.play(playable as unknown as Track[], position)
                    : undefined
                }
              >
                <div className="invite-main">
                  <span className="invite-label">
                    {item.title ?? item.filename}
                  </span>
                  <span className={`pill pill-${item.state}`}>
                    {STATE_LABEL[item.state] ?? item.state}
                  </span>
                </div>
                <div className="invite-meta num">
                  {item.uploaded_by} · {size(item.size_bytes)}
                  {item.source === "watcher" ? " · synced" : ""}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
