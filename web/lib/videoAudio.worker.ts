/// <reference lib="webworker" />

type EncodeRequest = {
  id: number;
  samples: ArrayBuffer;
  sampleRate: number;
  channels: number;
};

type EncodeResponse =
  | {id: number; ok: true; wav: ArrayBuffer}
  | {id: number; ok: false; error: string};

const worker = self as unknown as DedicatedWorkerGlobalScope;

function writeAscii(view: DataView, offset: number, value: string) {
  for (let index = 0; index < value.length; index += 1) {
    view.setUint8(offset + index, value.charCodeAt(index));
  }
}

function encodeWav(samples: Float32Array, sampleRate: number, channels: number) {
  const dataBytes = samples.length * 2;
  const buffer = new ArrayBuffer(44 + dataBytes);
  const view = new DataView(buffer);

  writeAscii(view, 0, "RIFF");
  view.setUint32(4, 36 + dataBytes, true);
  writeAscii(view, 8, "WAVE");
  writeAscii(view, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, channels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * channels * 2, true);
  view.setUint16(32, channels * 2, true);
  view.setUint16(34, 16, true);
  writeAscii(view, 36, "data");
  view.setUint32(40, dataBytes, true);

  for (let index = 0; index < samples.length; index += 1) {
    const value = Math.max(-1, Math.min(1, samples[index]));
    view.setInt16(
      44 + index * 2,
      value < 0 ? Math.round(value * 0x8000) : Math.round(value * 0x7fff),
      true,
    );
  }
  return buffer;
}

worker.onmessage = (event: MessageEvent<EncodeRequest>) => {
  const {id, samples, sampleRate, channels} = event.data;
  try {
    if (!Number.isInteger(sampleRate) || sampleRate <= 0) {
      throw new Error("invalid sample rate");
    }
    if (!Number.isInteger(channels) || channels <= 0) {
      throw new Error("invalid channel count");
    }
    const wav = encodeWav(new Float32Array(samples), sampleRate, channels);
    const response: EncodeResponse = {id, ok: true, wav};
    worker.postMessage(response, [wav]);
  } catch (problem) {
    const response: EncodeResponse = {
      id,
      ok: false,
      error: problem instanceof Error ? problem.message : "could not encode WAV",
    };
    worker.postMessage(response);
  }
};

export {};
