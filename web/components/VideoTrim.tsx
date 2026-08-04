"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import {IconClose, IconPause, IconPlay} from "./Icons";
import {
  SERVER_FALLBACK_MAX_BYTES,
  VideoAudioError,
  decodeVideoAudio,
  encodeWav,
  prepareVideoAudio,
  remuxAac,
  waveformPeaks,
  withOutputExtension,
  type DecodedPcm,
  type PreparedVideoAudio,
} from "@/lib/videoAudio";

type Props = {
  file: File;
  onClose: () => void;
  onExtracted: (file: File) => void;
  onServerFallback: (file: File) => void;
};

type LoadState =
  | {kind: "probing"}
  | {kind: "ready"; prepared: PreparedVideoAudio}
  | {kind: "fallback"; message: string}
  | {kind: "error"; message: string};

function time(value: number) {
  const minutes = Math.floor(Math.max(0, value) / 60);
  const seconds = Math.max(0, value) - minutes * 60;
  return `${minutes}:${seconds.toFixed(1).padStart(4, "0")}`;
}

function errorMessage(problem: unknown) {
  if (problem instanceof VideoAudioError) return problem.message;
  return problem instanceof Error ? problem.message : "could not read that video's audio";
}

function Waveform({
  pcm,
  start,
  end,
  onStart,
  onEnd,
}: {
  pcm: DecodedPcm;
  start: number;
  end: number;
  onStart: (value: number) => void;
  onEnd: (value: number) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const trackRef = useRef<HTMLDivElement | null>(null);
  const peaks = useMemo(() => waveformPeaks(pcm, 128), [pcm]);
  const minimum = Math.min(0.1, pcm.duration / 2);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const draw = () => {
      const bounds = canvas.getBoundingClientRect();
      const scale = window.devicePixelRatio || 1;
      canvas.width = Math.max(1, Math.round(bounds.width * scale));
      canvas.height = Math.max(1, Math.round(bounds.height * scale));
      const context = canvas.getContext("2d");
      if (!context) return;
      context.setTransform(scale, 0, 0, scale, 0, 0);
      context.clearRect(0, 0, bounds.width, bounds.height);
      context.strokeStyle = getComputedStyle(canvas).color;
      context.lineWidth = Math.max(1, bounds.width / peaks.length / 2.5);
      const middle = bounds.height / 2;
      const amplitude = Math.max(1, bounds.height / 2 - 8);
      const step = bounds.width / peaks.length;
      peaks.forEach((peak, index) => {
        const x = (index + 0.5) * step;
        context.beginPath();
        context.moveTo(x, middle + peak.min * amplitude);
        context.lineTo(x, middle + peak.max * amplitude);
        context.stroke();
      });
    };
    draw();
    const observer = new ResizeObserver(draw);
    observer.observe(canvas);
    return () => observer.disconnect();
  }, [peaks]);

  const move = useCallback(
    (which: "start" | "end", clientX: number) => {
      const bounds = trackRef.current?.getBoundingClientRect();
      if (!bounds) return;
      const raw = ((clientX - bounds.left) / bounds.width) * pcm.duration;
      if (which === "start") onStart(Math.max(0, Math.min(raw, end - minimum)));
      else onEnd(Math.min(pcm.duration, Math.max(raw, start + minimum)));
    },
    [end, minimum, onEnd, onStart, pcm.duration, start],
  );

  const pointerDown =
    (which: "start" | "end") => (event: ReactPointerEvent<HTMLButtonElement>) => {
      event.currentTarget.setPointerCapture(event.pointerId);
      move(which, event.clientX);
    };
  const pointerMove =
    (which: "start" | "end") => (event: ReactPointerEvent<HTMLButtonElement>) => {
      if (event.currentTarget.hasPointerCapture(event.pointerId)) {
        move(which, event.clientX);
      }
    };
  const keyboard =
    (which: "start" | "end") => (event: React.KeyboardEvent<HTMLButtonElement>) => {
      const direction = event.key === "ArrowLeft" ? -1 : event.key === "ArrowRight" ? 1 : 0;
      if (!direction) return;
      event.preventDefault();
      const step = event.shiftKey ? 1 : 0.1;
      if (which === "start") {
        onStart(Math.max(0, Math.min(start + direction * step, end - minimum)));
      } else {
        onEnd(Math.min(pcm.duration, Math.max(end + direction * step, start + minimum)));
      }
    };

  return (
    <div className="video-wave" ref={trackRef}>
      <canvas ref={canvasRef} aria-hidden="true" />
      <div
        className="video-wave-muted video-wave-muted-left"
        style={{width: `${(start / pcm.duration) * 100}%`}}
      />
      <div
        className="video-wave-muted video-wave-muted-right"
        style={{width: `${((pcm.duration - end) / pcm.duration) * 100}%`}}
      />
      <button
        type="button"
        className="video-handle is-start"
        style={{left: `${(start / pcm.duration) * 100}%`}}
        role="slider"
        aria-label="Trim start"
        aria-valuemin={0}
        aria-valuemax={Math.max(0, end - minimum)}
        aria-valuenow={start}
        aria-valuetext={time(start)}
        onPointerDown={pointerDown("start")}
        onPointerMove={pointerMove("start")}
        onKeyDown={keyboard("start")}
      >
        <i />
      </button>
      <button
        type="button"
        className="video-handle is-end"
        style={{left: `${(end / pcm.duration) * 100}%`}}
        role="slider"
        aria-label="Trim end"
        aria-valuemin={Math.min(pcm.duration, start + minimum)}
        aria-valuemax={pcm.duration}
        aria-valuenow={end}
        aria-valuetext={time(end)}
        onPointerDown={pointerDown("end")}
        onPointerMove={pointerMove("end")}
        onKeyDown={keyboard("end")}
      >
        <i />
      </button>
    </div>
  );
}

export function VideoTrim({file, onClose, onExtracted, onServerFallback}: Props) {
  const [load, setLoad] = useState<LoadState>({kind: "probing"});
  const [pcm, setPcm] = useState<DecodedPcm | null>(null);
  const [decoding, setDecoding] = useState(false);
  const [decodeProgress, setDecodeProgress] = useState(0);
  const [trimMode, setTrimMode] = useState(false);
  const [start, setStart] = useState(0);
  const [end, setEnd] = useState(0);
  const [filename, setFilename] = useState("");
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    closeRef.current?.focus();
    const controller = new AbortController();
    setLoad({kind: "probing"});
    void prepareVideoAudio(file, controller.signal)
      .then((prepared) => {
        setLoad({kind: "ready", prepared});
        setFilename(prepared.outputName);
        setEnd(prepared.duration);
        // Keep the AAC whole-file path decode-free. The waveform is decoded
        // only after the person explicitly chooses to trim.
        setTrimMode(!prepared.passthrough);
      })
      .catch((problem: unknown) => {
        if (controller.signal.aborted) return;
        if (
          problem instanceof VideoAudioError &&
          problem.code === "unsupported" &&
          file.size <= SERVER_FALLBACK_MAX_BYTES
        ) {
          setLoad({
            kind: "fallback",
            message: "This browser cannot read that video's audio, but the server can extract this small file.",
          });
          return;
        }
        if (problem instanceof VideoAudioError && problem.code === "unsupported") {
          setLoad({
            kind: "error",
            message: "this browser can't read that video's audio; AirDrop the file to Hareesh instead",
          });
          return;
        }
        setLoad({kind: "error", message: errorMessage(problem)});
      });
    return () => controller.abort();
  }, [file]);

  useEffect(() => {
    if (load.kind !== "ready" || !trimMode || pcm || decoding) return;
    const controller = new AbortController();
    setDecoding(true);
    setDecodeProgress(0);
    setError(null);
    void decodeVideoAudio(load.prepared, controller.signal, setDecodeProgress)
      .then((decoded) => {
        setPcm(decoded);
        setStart(0);
        setEnd(decoded.duration);
        setFilename((value) => withOutputExtension(value, ".wav"));
      })
      .catch((problem: unknown) => {
        if (!controller.signal.aborted) setError(errorMessage(problem));
      })
      .finally(() => {
        if (!controller.signal.aborted) setDecoding(false);
      });
    return () => controller.abort();
  }, [load, pcm, trimMode]);

  useEffect(() => {
    if (!pcm) return;
    let disposed = false;
    let url: string | null = null;
    void encodeWav(pcm)
      .then((blob) => {
        if (disposed) return;
        url = URL.createObjectURL(blob);
        setPreviewUrl(url);
      })
      .catch((problem: unknown) => {
        if (!disposed) setError(errorMessage(problem));
      });
    return () => {
      disposed = true;
      if (url) URL.revokeObjectURL(url);
      const audio = audioRef.current;
      if (audio) {
        audio.pause();
        audio.removeAttribute("src");
        audio.load();
      }
      setPreviewUrl(null);
    };
  }, [pcm]);

  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !working) onClose();
    };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [onClose, working]);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio || audio.paused) return;
    audio.pause();
    audio.currentTime = start;
    setPlaying(false);
  }, [start, end]);

  const togglePreview = useCallback(async () => {
    const audio = audioRef.current;
    if (!audio || !previewUrl) return;
    if (!audio.paused) {
      audio.pause();
      setPlaying(false);
      return;
    }
    audio.currentTime = start;
    try {
      await audio.play();
      setPlaying(true);
    } catch {
      setError("could not play the preview");
    }
  }, [previewUrl, start]);

  const extracted = useCallback(
    async (whole: boolean) => {
      if (load.kind !== "ready" || working) return;
      setWorking(true);
      setError(null);
      try {
        const usePassthrough = whole && load.prepared.passthrough;
        const blob = usePassthrough
          ? await remuxAac(load.prepared)
          : pcm
            ? await encodeWav(pcm, whole ? 0 : start, whole ? pcm.duration : end)
            : null;
        if (!blob) throw new Error("choose Trim to decode this video's audio");
        const extension = usePassthrough ? ".m4a" : ".wav";
        const name = withOutputExtension(filename, extension);
        onExtracted(new File([blob], name, {type: blob.type, lastModified: Date.now()}));
      } catch (problem) {
        setError(errorMessage(problem));
        setWorking(false);
      }
    },
    [end, filename, load, onExtracted, pcm, start, working],
  );

  const dialogTitle = load.kind === "ready" ? "Extract audio" : "Video audio";

  return (
    <div className="scrim" onClick={working ? undefined : onClose}>
      <div
        className="dialog video-trim-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="video-trim-title"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="video-trim-head">
          <div>
            <h2 className="dialog-title" id="video-trim-title">{dialogTitle}</h2>
            <p className="dialog-sub">{file.name}</p>
          </div>
          <button
            ref={closeRef}
            type="button"
            className="video-trim-close"
            aria-label="Close"
            title="Close"
            disabled={working}
            onClick={onClose}
          >
            <IconClose size={18} />
          </button>
        </div>

        {load.kind === "probing" ? (
          <p className="video-trim-status">Reading the audio track…</p>
        ) : load.kind === "fallback" ? (
          <>
            <p className="video-trim-status">{load.message}</p>
            <div className="dialog-actions">
              <button className="btn btn-quiet" type="button" onClick={onClose}>
                Cancel
              </button>
              <button
                className="btn btn-main"
                type="button"
                onClick={() => onServerFallback(file)}
              >
                Extract on server
              </button>
            </div>
          </>
        ) : load.kind === "error" ? (
          <p className="video-trim-error">{load.message}</p>
        ) : (
          <>
            {trimMode ? (
              decoding ? (
                <div className="video-trim-loading">
                  <span>Decoding audio…</span>
                  <span className="num">{Math.round(decodeProgress * 100)}%</span>
                </div>
              ) : pcm ? (
                <>
                  <Waveform
                    pcm={pcm}
                    start={start}
                    end={end}
                    onStart={setStart}
                    onEnd={setEnd}
                  />
                  <div className="video-trim-readout">
                    <span className="num">{time(start)}</span>
                    <button
                      type="button"
                      className="video-preview"
                      aria-label={playing ? "Pause preview" : "Play preview"}
                      title={playing ? "Pause preview" : "Play preview"}
                      disabled={!previewUrl}
                      onClick={() => void togglePreview()}
                    >
                      {playing ? <IconPause size={18} /> : <IconPlay size={18} />}
                    </button>
                    <span className="num">{time(end)}</span>
                  </div>
                  <audio
                    ref={audioRef}
                    src={previewUrl ?? undefined}
                    preload="auto"
                    onPause={() => setPlaying(false)}
                    onEnded={() => setPlaying(false)}
                    onTimeUpdate={(event) => {
                      if (event.currentTarget.currentTime >= end) {
                        event.currentTarget.pause();
                        event.currentTarget.currentTime = start;
                      }
                    }}
                  />
                </>
              ) : null
            ) : (
              <div className="video-passthrough">
                <p>AAC audio can come out exactly as recorded, without transcoding.</p>
                <button
                  type="button"
                  className="btn btn-quiet"
                  onClick={() => setTrimMode(true)}
                >
                  Trim it
                </button>
              </div>
            )}

            <label className="field video-filename">
              <span className="field-label">Filename</span>
              <input
                className="input"
                value={filename}
                maxLength={124}
                disabled={working}
                onChange={(event) => setFilename(event.target.value)}
              />
            </label>

            {error ? <p className="video-trim-error">{error}</p> : null}

            <div className="dialog-actions video-trim-actions">
              <button
                className="btn btn-quiet"
                type="button"
                disabled={
                  working ||
                  decoding ||
                  (!load.prepared.passthrough && !pcm)
                }
                onClick={() => void extracted(true)}
              >
                {working ? "Extracting…" : "Use the whole thing"}
              </button>
              {trimMode ? (
                <button
                  className="btn btn-main"
                  type="button"
                  disabled={working || decoding || !pcm || end <= start}
                  onClick={() => void extracted(false)}
                >
                  Upload trim
                </button>
              ) : null}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
