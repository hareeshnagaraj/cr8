import {
  ALL_FORMATS,
  AudioSampleSink,
  BlobSource,
  BufferTarget,
  EncodedAudioPacketSource,
  EncodedPacketSink,
  Input,
  Mp4OutputFormat,
  Output,
  type AudioCodec,
} from "mediabunny";

export const VIDEO_EXTENSIONS = new Set([".mp4", ".mov", ".webm", ".mkv"]);
export const SERVER_FALLBACK_MAX_BYTES = 95 * 1024 * 1024;

export type PreparedVideoAudio = {
  file: File;
  codec: AudioCodec;
  duration: number;
  passthrough: boolean;
  outputName: string;
};

export type DecodedPcm = {
  channels: number;
  sampleRate: number;
  samples: Float32Array;
  duration: number;
};

export type VideoAudioErrorCode =
  | "unsupported"
  | "no-audio"
  | "invalid"
  | "canceled";

export class VideoAudioError extends Error {
  constructor(
    readonly code: VideoAudioErrorCode,
    message: string,
  ) {
    super(message);
    this.name = "VideoAudioError";
  }
}

function checkCanceled(signal?: AbortSignal) {
  if (signal?.aborted) {
    throw new VideoAudioError("canceled", "video extraction was canceled");
  }
}

function stemOf(filename: string) {
  const leaf = filename.replaceAll("\\", "/").split("/").pop() || "video";
  const dot = leaf.lastIndexOf(".");
  return (dot > 0 ? leaf.slice(0, dot) : leaf).trim() || "video";
}

export function outputFilename(filename: string, extension: ".m4a" | ".wav") {
  return `${stemOf(filename)}${extension}`;
}

export function isVideoFile(file: File) {
  const dot = file.name.lastIndexOf(".");
  return VIDEO_EXTENSIONS.has(dot >= 0 ? file.name.slice(dot).toLowerCase() : "");
}

async function openAudioTrack(file: File) {
  const input = new Input({
    source: new BlobSource(file),
    formats: ALL_FORMATS,
  });
  try {
    const track = await input.getPrimaryAudioTrack();
    if (!track) {
      throw new VideoAudioError("no-audio", "that video does not have an audio track");
    }
    return {input, track};
  } catch (problem) {
    input.dispose();
    if (problem instanceof VideoAudioError) throw problem;
    throw new VideoAudioError(
      "unsupported",
      problem instanceof Error ? problem.message : "this video format is not supported",
    );
  }
}

export async function prepareVideoAudio(
  file: File,
  signal?: AbortSignal,
): Promise<PreparedVideoAudio> {
  checkCanceled(signal);
  if (!("AudioDecoder" in globalThis)) {
    throw new VideoAudioError(
      "unsupported",
      "this browser does not support video audio extraction",
    );
  }

  const {input, track} = await openAudioTrack(file);
  try {
    checkCanceled(signal);
    const [codec, duration, canDecode] = await Promise.all([
      track.getCodec(),
      track.computeDuration(),
      track.canDecode(),
    ]);
    checkCanceled(signal);
    if (!codec || !canDecode) {
      throw new VideoAudioError(
        "unsupported",
        "this browser cannot decode that video's audio",
      );
    }
    if (!Number.isFinite(duration) || duration <= 0) {
      throw new VideoAudioError("invalid", "that video's audio has no duration");
    }
    const passthrough = codec === "aac";
    return {
      file,
      codec,
      duration,
      passthrough,
      outputName: outputFilename(file.name, passthrough ? ".m4a" : ".wav"),
    };
  } catch (problem) {
    if (problem instanceof VideoAudioError) throw problem;
    throw new VideoAudioError(
      "unsupported",
      problem instanceof Error
        ? problem.message
        : "this browser cannot decode that video's audio",
    );
  } finally {
    input.dispose();
  }
}

export async function decodeVideoAudio(
  prepared: PreparedVideoAudio,
  signal?: AbortSignal,
  onProgress?: (progress: number) => void,
): Promise<DecodedPcm> {
  checkCanceled(signal);
  const {input, track} = await openAudioTrack(prepared.file);
  try {
    if (!(await track.canDecode())) {
      throw new VideoAudioError(
        "unsupported",
        "this browser cannot decode that video's audio",
      );
    }

    const sink = new AudioSampleSink(track);
    const chunks: Float32Array[] = [];
    let channels = 0;
    let sampleRate = 0;
    let totalValues = 0;

    for await (const sample of sink.samples()) {
      try {
        checkCanceled(signal);
        if (!channels) {
          channels = sample.numberOfChannels;
          sampleRate = sample.sampleRate;
        }
        if (
          sample.numberOfChannels !== channels ||
          sample.sampleRate !== sampleRate
        ) {
          throw new VideoAudioError(
            "unsupported",
            "that video's audio changes format partway through",
          );
        }
        const values = new Float32Array(sample.numberOfFrames * channels);
        sample.copyTo(values, {planeIndex: 0, format: "f32"});
        chunks.push(values);
        totalValues += values.length;
        onProgress?.(
          Math.min(1, Math.max(0, (sample.timestamp + sample.duration) / prepared.duration)),
        );
      } finally {
        sample.close();
      }
    }

    if (!channels || !sampleRate || !totalValues) {
      throw new VideoAudioError("no-audio", "that video does not have readable audio");
    }
    const samples = new Float32Array(totalValues);
    let offset = 0;
    for (const chunk of chunks) {
      samples.set(chunk, offset);
      offset += chunk.length;
    }
    onProgress?.(1);
    return {
      channels,
      sampleRate,
      samples,
      duration: totalValues / channels / sampleRate,
    };
  } finally {
    input.dispose();
  }
}

export async function remuxAac(
  prepared: PreparedVideoAudio,
  signal?: AbortSignal,
): Promise<Blob> {
  if (!prepared.passthrough) {
    throw new VideoAudioError("invalid", "only AAC audio can use passthrough");
  }
  checkCanceled(signal);
  const {input, track} = await openAudioTrack(prepared.file);
  const target = new BufferTarget();
  const output = new Output({
    format: new Mp4OutputFormat({fastStart: "in-memory"}),
    target,
  });
  const source = new EncodedAudioPacketSource("aac");
  output.addAudioTrack(source);
  try {
    const codec = await track.getCodec();
    if (codec !== "aac") {
      throw new VideoAudioError("invalid", "the source audio is not AAC");
    }
    const decoderConfig = await track.getDecoderConfig();
    await output.start();
    const sink = new EncodedPacketSink(track);
    let firstTimestamp: number | null = null;
    for await (const packet of sink.packets()) {
      checkCanceled(signal);
      if (firstTimestamp === null) firstTimestamp = packet.timestamp;
      const base = firstTimestamp;
      const shifted = packet.clone({
        timestamp: Math.max(0, packet.timestamp - base),
      });
      await source.add(shifted, {decoderConfig: decoderConfig ?? undefined});
    }
    source.close();
    await output.finalize();
    if (!target.buffer) {
      throw new VideoAudioError("invalid", "could not finish the AAC audio file");
    }
    return new Blob([target.buffer], {type: "audio/mp4"});
  } catch (problem) {
    if (output.state === "started") await output.cancel();
    throw problem;
  } finally {
    input.dispose();
  }
}

let workerRequest = 0;

export async function encodeWav(
  pcm: DecodedPcm,
  startSeconds = 0,
  endSeconds = pcm.duration,
): Promise<Blob> {
  const totalFrames = Math.floor(pcm.samples.length / pcm.channels);
  const firstFrame = Math.max(
    0,
    Math.min(Math.floor(startSeconds * pcm.sampleRate), totalFrames - 1),
  );
  const lastFrame = Math.min(
    totalFrames,
    Math.max(
    firstFrame + 1,
      Math.ceil(endSeconds * pcm.sampleRate),
    ),
  );
  const selected = pcm.samples.slice(
    firstFrame * pcm.channels,
    lastFrame * pcm.channels,
  );
  const id = ++workerRequest;
  const worker = new Worker(new URL("./videoAudio.worker.ts", import.meta.url));
  try {
    const wav = await new Promise<ArrayBuffer>((resolve, reject) => {
      worker.onmessage = (
        event: MessageEvent<
          | {id: number; ok: true; wav: ArrayBuffer}
          | {id: number; ok: false; error: string}
        >,
      ) => {
        if (event.data.id !== id) return;
        if (event.data.ok) resolve(event.data.wav);
        else reject(new Error(event.data.error));
      };
      worker.onerror = () => reject(new Error("the WAV encoder stopped unexpectedly"));
      worker.postMessage(
        {
          id,
          samples: selected.buffer,
          sampleRate: pcm.sampleRate,
          channels: pcm.channels,
        },
        [selected.buffer],
      );
    });
    return new Blob([wav], {type: "audio/wav"});
  } finally {
    worker.terminate();
  }
}

export function waveformPeaks(pcm: DecodedPcm, barCount: number) {
  const count = Math.max(1, Math.floor(barCount));
  const frames = pcm.samples.length / pcm.channels;
  const framesPerBar = Math.max(1, Math.ceil(frames / count));
  const peaks: {min: number; max: number}[] = [];
  for (let bar = 0; bar < count; bar += 1) {
    const first = bar * framesPerBar;
    const last = Math.min(frames, first + framesPerBar);
    let min = 0;
    let max = 0;
    for (let frame = first; frame < last; frame += 1) {
      let mixed = 0;
      for (let channel = 0; channel < pcm.channels; channel += 1) {
        mixed += pcm.samples[frame * pcm.channels + channel] / pcm.channels;
      }
      min = Math.min(min, mixed);
      max = Math.max(max, mixed);
    }
    peaks.push({min, max});
  }
  return peaks;
}

export function withOutputExtension(name: string, extension: ".m4a" | ".wav") {
  const cleaned = name.replaceAll("\\", "/").split("/").pop()?.trim() || "video";
  const stem = cleaned.replace(/\.[^.]+$/, "").trim() || "video";
  return `${stem}${extension}`;
}
