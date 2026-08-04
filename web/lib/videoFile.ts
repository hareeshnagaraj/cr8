// Deliberately dependency-free: the upload page imports THIS to recognize a
// video, and only a recognized video lazily pulls the heavy extract stack
// (mediabunny + WebCodecs plumbing) via dynamic import. Keeping this check
// out of videoAudio.ts keeps half a megabyte off every visitor's first load.
const VIDEO_EXTENSIONS = new Set([".mp4", ".mov", ".webm", ".mkv", ".m4v"]);

export function isVideoFile(file: File): boolean {
  const dot = file.name.lastIndexOf(".");
  return VIDEO_EXTENSIONS.has(dot >= 0 ? file.name.slice(dot).toLowerCase() : "");
}
