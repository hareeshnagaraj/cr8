// Inline SVG, sized by currentColor and a shared stroke weight. No icon font, no
// package, no network request: each of these is a few hundred bytes in the bundle
// and paints with the rest of the markup.
type P = {size?: number};

const base = (size: number) => ({
  width: size,
  height: size,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.6,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
});

export const IconShare = ({size = 15}: P) => (
  <svg {...base(size)}>
    <path d="M12 3v12" />
    <path d="M8 7l4-4 4 4" />
    <path d="M5 13v6a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-6" />
  </svg>
);

export const IconStems = ({size = 15}: P) => (
  // Four bars of unequal height: the shape of a split into parts.
  <svg {...base(size)}>
    <path d="M5 9v6" />
    <path d="M10 5v14" />
    <path d="M14 8v8" />
    <path d="M19 11v2" />
  </svg>
);

export const IconDownload = ({size = 15}: P) => (
  <svg {...base(size)}>
    <path d="M12 4v11" />
    <path d="M8 11l4 4 4-4" />
    <path d="M5 20h14" />
  </svg>
);

export const IconHeart = ({size = 15, filled = false}: P & {filled?: boolean}) => (
  <svg {...base(size)} fill={filled ? "currentColor" : "none"}>
    <path d="M12 20s-7-4.6-7-9.1A3.9 3.9 0 0 1 12 8a3.9 3.9 0 0 1 7 2.9C19 15.4 12 20 12 20z" />
  </svg>
);

export const IconClose = ({size = 15}: P) => (
  <svg {...base(size)}>
    <path d="M6 6l12 12" />
    <path d="M18 6L6 18" />
  </svg>
);

export const IconCheck = ({size = 15}: P) => (
  <svg {...base(size)}>
    <path d="M5 12.5l4.5 4.5L19 7" />
  </svg>
);

export const IconLink = ({size = 15}: P) => (
  <svg {...base(size)}>
    <path d="M10.5 13.5a4 4 0 0 0 5.7 0l2.3-2.3a4 4 0 0 0-5.7-5.7l-1.3 1.3" />
    <path d="M13.5 10.5a4 4 0 0 0-5.7 0l-2.3 2.3a4 4 0 0 0 5.7 5.7l1.3-1.3" />
  </svg>
);

export const IconBack = ({size = 15}: P) => (
  <svg {...base(size)}>
    <path d="M15 5l-7 7 7 7" />
  </svg>
);

export const IconLayers = ({size = 15}: P) => (
  // Stacked plates: one song, several versions of it.
  <svg {...base(size)}>
    <path d="M12 3l9 5-9 5-9-5 9-5z" />
    <path d="M3 13l9 5 9-5" />
  </svg>
);

export const IconClock = ({size = 15}: P) => (
  <svg {...base(size)}>
    <circle cx="12" cy="12" r="8" />
    <path d="M12 7v5l3 2" />
  </svg>
);

export const IconPlay = ({size = 15}: P) => (
  <svg {...base(size)}>
    <path d="M8 5l11 7-11 7V5z" />
  </svg>
);

export const IconPause = ({size = 15}: P) => (
  <svg {...base(size)}>
    <path d="M8 5v14" />
    <path d="M16 5v14" />
  </svg>
);

export const IconPrev = ({size = 15}: P) => (
  <svg {...base(size)}>
    <path d="M6 5v14" />
    <path d="M18 5l-9 7 9 7V5z" />
  </svg>
);

export const IconNext = ({size = 15}: P) => (
  <svg {...base(size)}>
    <path d="M18 5v14" />
    <path d="M6 5l9 7-9 7V5z" />
  </svg>
);

export const IconSend = ({size = 15}: P) => (
  <svg {...base(size)}>
    <path d="M21 3L10 14" />
    <path d="M21 3l-7 18-4-7-7-4 18-7z" />
  </svg>
);

export const IconTag = ({size = 15}: P) => (
  <svg {...base(size)}>
    <path d="M20 13l-7 7-9-9V4h7l9 9z" />
    <circle cx="8.5" cy="8.5" r="1" />
  </svg>
);

export const IconShuffle = ({size = 15}: P) => (
  <svg {...base(size)}>
    <path d="M4 7h3c4 0 6 10 10 10h3" />
    <path d="M17 14l3 3-3 3" />
    <path d="M4 17h3c1.6 0 2.9-1.6 4.2-3.6" />
    <path d="M13 9.8C14.2 8.2 15.4 7 17 7h3" />
    <path d="M17 4l3 3-3 3" />
  </svg>
);

export const IconFilter = ({size = 15}: P) => (
  <svg {...base(size)}>
    <path d="M4 5h16l-6 7v6l-4 2v-8L4 5z" />
  </svg>
);

export const IconSearch = ({size = 15}: P) => (
  <svg {...base(size)}>
    <circle cx="10.5" cy="10.5" r="6.5" />
    <path d="M15.5 15.5L21 21" />
  </svg>
);

export const IconMore = ({size = 15}: P) => (
  <svg {...base(size)}>
    <circle cx="5" cy="12" r="1" fill="currentColor" stroke="none" />
    <circle cx="12" cy="12" r="1" fill="currentColor" stroke="none" />
    <circle cx="19" cy="12" r="1" fill="currentColor" stroke="none" />
  </svg>
);
