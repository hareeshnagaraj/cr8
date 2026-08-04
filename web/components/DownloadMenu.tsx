"use client";

import {useEffect, useRef} from "react";

import {IconDownload} from "./Icons";
import {Menu, type MenuOption} from "./Menu";
import type {Track} from "./PlayerProvider";

type DownloadStem = {
  bounce_ulid?: string;
  stem_kind?: string;
  title?: string;
};

export function DownloadMenu({
  track,
  stems = [],
  className = "",
  iconOnly = false,
}: {
  track: Pick<Track, "bounce_ulid">;
  stems?: DownloadStem[];
  className?: string;
  iconOnly?: boolean;
}) {
  const links = [
    {
      label: "Original",
      href: `/download/${track.bounce_ulid}?format=original`,
    },
    {
      label: "mp3 320",
      href: `/download/${track.bounce_ulid}?format=mp3`,
    },
    ...stems.flatMap((stem) =>
      stem.bounce_ulid
        ? [{
            label: stem.stem_kind ?? stem.title ?? "stem",
            href: `/download/stem/${stem.bounce_ulid}`,
          }]
        : [],
    ),
  ];
  const anchors = useRef(new Map<string, HTMLAnchorElement>());
  const options: MenuOption[] = links.map(({label, href}) => ({
    label,
    value: href,
  }));
  const menuRoot = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!iconOnly) return;
    const trigger = menuRoot.current?.querySelector<HTMLButtonElement>(".menu-trigger");
    trigger?.setAttribute("aria-label", "Download");
    trigger?.setAttribute("title", "Download");
  }, [iconOnly]);

  return (
    <div
      ref={menuRoot}
      className={`download-menu${className ? ` ${className}` : ""}`}
    >
      <Menu
        label={iconOnly ? (
          <span className="download-icon-label">
            <IconDownload />
            <span className="sr-only">Download</span>
          </span>
        ) : "Download"}
        options={options}
        value=""
        onChange={(href) => anchors.current.get(href)?.click()}
      />
      <div hidden>
        {links.map(({label, href}) => (
          <a
            key={href}
            href={href}
            ref={(node) => {
              if (node) anchors.current.set(href, node);
              else anchors.current.delete(href);
            }}
          >
            {label}
          </a>
        ))}
      </div>
    </div>
  );
}
