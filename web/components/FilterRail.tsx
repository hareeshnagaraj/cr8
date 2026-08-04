"use client";

import {
  useEffect,
  useMemo,
  useState,
  type CSSProperties,
} from "react";

import {BpmPopover, BpmRange} from "@/components/BpmRange";
import {IconClose, IconFilter, IconSearch, IconShuffle} from "@/components/Icons";
import {Menu, type MenuOption} from "@/components/Menu";
import {dimensionActive, dimensionWash} from "@/lib/colors";
import {releaseFocus} from "@/lib/focus";

type FacetsPayload = {
  status: {value: string; count: number}[];
  tags: Record<string, {value: string; count: number}[]>;
};

type FacetCtx = {
  facets: FacetsPayload | null;
  eras: Record<string, string>;
  keys: string[];
};

function facetOptions(
  values: {value: string; count: number}[] | undefined,
): MenuOption[] {
  return (values ?? []).map((item) => ({value: item.value, count: item.count}));
}

const KEEPER_OPTIONS: MenuOption[] = [
  {value: "", label: "Any"},
  ...[1, 2, 3, 4, 5].map((value) => ({value: String(value), label: `${value}+`})),
];

type Facet =
  | {
      dim: "status" | "keeperMin" | "era" | "key";
      label: string;
      kind: "single";
      param: string;
      options: (ctx: FacetCtx) => MenuOption[];
      searchable?: boolean;
      pill?: (value: string) => string;
    }
  | {
      dim: "vibe" | "instr" | "collab" | "use";
      label: string;
      kind: "multi";
      param: string;
      options: (ctx: FacetCtx) => MenuOption[];
      searchable?: boolean;
      colored: true;
    }
  | {
      dim: "unheard" | "hearted";
      label: string;
      kind: "flag";
      param: string;
      pill?: () => string;
      barLabel?: string;
      barClass?: string;
      barAriaLabel?: string;
    };

const FACETS: readonly Facet[] = [
  {
    dim: "status",
    label: "Status",
    kind: "single",
    param: "status",
    options: (ctx) => facetOptions(ctx.facets?.status),
  },
  {
    dim: "keeperMin",
    label: "Keeper",
    kind: "single",
    param: "keeper_min",
    options: () => KEEPER_OPTIONS,
    pill: (value) => `keeper ${value}+`,
  },
  {
    dim: "era",
    label: "Era",
    kind: "single",
    param: "era",
    // Always mounted on desktop even while eras load — empty menu beats CLS.
    options: (ctx) =>
      Object.entries(ctx.eras)
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([value, swatch]) => ({value, swatch})),
  },
  {
    dim: "key",
    label: "Key",
    kind: "single",
    param: "key",
    searchable: true,
    options: (ctx) => ctx.keys.map((value) => ({value})),
  },
  {
    dim: "vibe",
    label: "Vibe",
    kind: "multi",
    param: "vibe",
    searchable: true,
    colored: true,
    options: (ctx) => facetOptions(ctx.facets?.tags?.vibe),
  },
  {
    dim: "instr",
    label: "Instruments",
    kind: "multi",
    param: "instr",
    searchable: true,
    colored: true,
    options: (ctx) => facetOptions(ctx.facets?.tags?.instr),
  },
  {
    dim: "collab",
    label: "Collab",
    kind: "multi",
    param: "collab",
    searchable: true,
    colored: true,
    options: (ctx) => facetOptions(ctx.facets?.tags?.collab),
  },
  {
    dim: "use",
    label: "Use",
    kind: "multi",
    param: "use",
    searchable: true,
    colored: true,
    options: (ctx) => facetOptions(ctx.facets?.tags?.use),
  },
  {dim: "unheard", label: "unheard", kind: "flag", param: "unheard"},
  {
    dim: "hearted",
    label: "Hearted",
    kind: "flag",
    param: "hearted",
    pill: () => "♥ hearted",
    barLabel: "♥",
    barClass: "filter-heart",
    barAriaLabel: "Hearted songs",
  },
];

export type Filters = {
  [F in Facet as F["dim"]]: F["kind"] extends "multi"
    ? string[]
    : F["kind"] extends "flag"
      ? boolean
      : string;
};

export const NO_FILTERS = Object.fromEntries(
  FACETS.map((f) => [f.dim, f.kind === "multi" ? [] : f.kind === "flag" ? false : ""]),
) as unknown as Filters;

export type SortOption = {
  key: string;
  label: string;
  asc: string;
  desc: string;
  initial?: "asc" | "desc";
  fixed?: boolean;
  column?: {cls: string};
};

export function nextSort(current: string, option: SortOption): string {
  if (option.fixed) return option.asc;
  if (current === option.asc) return option.desc;
  if (current === option.desc) return option.asc;
  return option.initial === "desc" ? option.desc : option.asc;
}

function activeValueCount(filters: Filters) {
  return FACETS.reduce((n, f) => {
    if (f.kind === "multi") return n + filters[f.dim].length;
    return n + (filters[f.dim] ? 1 : 0);
  }, 0);
}

function dimensionStyle(dim?: string): CSSProperties {
  return {
    ["--filter-wash" as string]: (dim && dimensionWash(dim)) || "rgba(255, 255, 255, 0.09)",
    ["--filter-active" as string]: (dim && dimensionActive(dim)) || "var(--accent)",
  };
}

function setSingle(filters: Filters, dim: Extract<Facet, {kind: "single"}>["dim"], value: string): Filters {
  return {...filters, [dim]: filters[dim] === value ? "" : value};
}

function setMulti(filters: Filters, dim: Extract<Facet, {kind: "multi"}>["dim"], value: string): Filters {
  const cur = filters[dim];
  return {
    ...filters,
    [dim]: cur.includes(value) ? cur.filter((v) => v !== value) : [...cur, value],
  };
}

function clearDim(filters: Filters, dim: Facet["dim"]): Filters {
  const f = FACETS.find((item) => item.dim === dim)!;
  return {...filters, [dim]: f.kind === "multi" ? [] : f.kind === "flag" ? false : ""};
}

type LibraryFilterBarProps = {
  filters: Filters;
  onChange: (next: Filters) => void;
  bpm: [number | null, number | null];
  bpmFloor: number;
  bpmCeiling: number;
  histogram: number[];
  onBpmChange: (low: number, high: number) => void;
  query: string;
  onQueryChange: (query: string) => void;
  onShuffle: () => void;
  sort: string;
  sortOptions: readonly SortOption[];
  onSortChange: (sort: string) => void;
  eras: Record<string, string>;
  keys: string[];
};

function FacetMenu({
  facet,
  options,
  filters,
  onChange,
}: {
  facet: Extract<Facet, {kind: "single" | "multi"}>;
  options: MenuOption[];
  filters: Filters;
  onChange: (next: Filters) => void;
}) {
  return (
    <span className="filterbar-menu">
      <span className="filterbar-separator" aria-hidden="true">·</span>
      {facet.kind === "multi" ? (
        <Menu
          label={facet.label}
          options={options}
          value={filters[facet.dim]}
          badge={filters[facet.dim].length || undefined}
          searchable={facet.searchable}
          activeColor={dimensionActive(facet.dim)}
          activeWash={dimensionWash(facet.dim)}
          onChange={(values) => onChange({...filters, [facet.dim]: values})}
        />
      ) : (
        <Menu
          label={facet.label}
          options={options}
          value={filters[facet.dim]}
          badge={filters[facet.dim] ? 1 : undefined}
          searchable={facet.searchable}
          onChange={(value) => onChange(setSingle(filters, facet.dim, value))}
        />
      )}
    </span>
  );
}

function FacetChips({
  filters,
  onChange,
  ctx,
}: {
  filters: Filters;
  onChange: (next: Filters) => void;
  ctx: FacetCtx;
}) {
  return (
    <>
      {FACETS.map((facet) => {
        if (facet.kind === "flag") return null;
        const options =
          facet.kind === "single"
            ? facet.options(ctx).filter((o) => o.value)
            : facet.options(ctx);
        if (!options.length && facet.dim !== "status") return null;
        const colored = facet.kind === "multi";

        return (
          <section className="filter-sheet-section" key={facet.dim}>
            <h3 className="fgroup-label">{facet.label}</h3>
            <div className="chips">
              {options.map((item) => {
                const selected =
                  facet.kind === "multi"
                    ? filters[facet.dim].includes(item.value)
                    : filters[facet.dim] === item.value;
                return (
                  <button
                    key={item.value}
                    className={`chip${colored ? " facet-chip" : ""}${selected ? " is-on" : ""}`}
                    type="button"
                    aria-pressed={selected}
                    disabled={item.count !== undefined && !item.count}
                    style={colored ? dimensionStyle(facet.dim) : undefined}
                    onClick={() =>
                      onChange(
                        facet.kind === "multi"
                          ? setMulti(filters, facet.dim, item.value)
                          : setSingle(filters, facet.dim, item.value),
                      )
                    }
                  >
                    {item.swatch ? (
                      <span
                        className="menu-swatch"
                        style={{backgroundColor: item.swatch, marginRight: 6}}
                        aria-hidden="true"
                      />
                    ) : null}
                    {item.label ?? item.value}
                    {item.count !== undefined ? (
                      <span className="chip-count num">{item.count}</span>
                    ) : null}
                  </button>
                );
              })}
            </div>
          </section>
        );
      })}
      <section className="filter-sheet-section">
        <h3 className="fgroup-label">Listening</h3>
        <div className="chips">
          {FACETS.filter((f) => f.kind === "flag").map((facet) => {
            const on = filters[facet.dim];
            return (
              <button
                key={facet.dim}
                className={`chip${on ? " is-on" : ""}`}
                type="button"
                aria-pressed={on}
                onClick={() => onChange({...filters, [facet.dim]: !on})}
              >
                {facet.dim === "hearted" ? (
                  <>
                    <span aria-hidden="true">♥</span> Hearted
                  </>
                ) : (
                  "Unheard"
                )}
              </button>
            );
          })}
        </div>
      </section>
    </>
  );
}

export function LibraryFilterBar({
  filters,
  onChange,
  bpm,
  bpmFloor,
  bpmCeiling,
  histogram,
  onBpmChange,
  query,
  onQueryChange,
  onShuffle,
  sort,
  sortOptions,
  onSortChange,
  eras,
  keys,
}: LibraryFilterBarProps) {
  const [facets, setFacets] = useState<FacetsPayload | null>(null);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(Boolean(query));
  const low = bpm[0] ?? bpmFloor;
  const high = bpm[1] ?? bpmCeiling;
  const bpmActive = bpm[0] !== null || bpm[1] !== null;
  const activeCount = activeValueCount(filters) + (bpmActive ? 1 : 0);
  const ctx = useMemo<FacetCtx>(() => ({facets, eras, keys}), [facets, eras, keys]);

  useEffect(() => {
    fetch("/api/facets", {credentials: "same-origin"})
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then(setFacets)
      .catch(() => setFacets(null));
  }, []);

  function closeSheet() {
    setSheetOpen(false);
    releaseFocus();
  }

  function closeSearch() {
    setSearchOpen(false);
    releaseFocus();
  }

  useEffect(() => {
    if (!sheetOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeSheet();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [sheetOpen]);

  const optionMap = useMemo(() => {
    const map: Record<string, MenuOption[]> = {};
    for (const f of FACETS) if (f.kind !== "flag") map[f.dim] = f.options(ctx);
    return map;
  }, [ctx]);

  const sortMenuOptions = useMemo(
    () =>
      sortOptions.flatMap((o) =>
        o.fixed
          ? [{value: o.asc, label: o.label}]
          : [
              {value: o.asc, label: `${o.label} ▲`},
              {value: o.desc, label: `${o.label} ▼`},
            ],
      ),
    [sortOptions],
  );

  function clearAll() {
    onChange(NO_FILTERS);
    onBpmChange(bpmFloor, bpmCeiling);
  }

  const pills: {key: string; label: string; dim?: string; remove: () => void}[] = [
    ...(bpmActive
      ? [{key: "tempo", label: `${low}–${high}`, remove: () => onBpmChange(bpmFloor, bpmCeiling)}]
      : []),
    ...FACETS.flatMap((facet) => {
      if (facet.kind === "single") {
        const value = filters[facet.dim];
        if (!value) return [];
        return [{
          key: `${facet.dim}-${value}`,
          label: facet.pill ? facet.pill(value) : value,
          remove: () => onChange(clearDim(filters, facet.dim)),
        }];
      }
      if (facet.kind === "multi") {
        return filters[facet.dim].map((value) => ({
          key: `${facet.dim}-${value}`,
          label: value,
          dim: facet.dim,
          remove: () => onChange(setMulti(filters, facet.dim, value)),
        }));
      }
      if (!filters[facet.dim]) return [];
      return [{
        key: facet.dim,
        label: facet.pill ? facet.pill() : facet.label,
        remove: () => onChange(clearDim(filters, facet.dim)),
      }];
    }),
  ];

  return (
    <>
      <div className="library-filterbar desktop-only">
        <div className="filterbar-line" aria-label="Library filters">
          <span className="filterbar-label">Tempo</span>
          <BpmPopover
            low={low}
            high={high}
            min={bpmFloor}
            max={bpmCeiling}
            histogram={histogram}
            onChange={onBpmChange}
          />
          {FACETS.map((facet) =>
            facet.kind === "flag" ? null : (
              <FacetMenu
                key={facet.dim}
                facet={facet}
                options={optionMap[facet.dim] ?? []}
                filters={filters}
                onChange={onChange}
              />
            ),
          )}
          <span className="filterbar-separator" aria-hidden="true">·</span>
          {FACETS.filter((f) => f.kind === "flag").map((facet) => {
            const on = filters[facet.dim];
            return (
              <button
                key={facet.dim}
                className={`filter-toggle${facet.barClass ? ` ${facet.barClass}` : ""}${on ? " is-on" : ""}`}
                type="button"
                aria-label={facet.barAriaLabel}
                aria-pressed={on}
                onClick={() => onChange({...filters, [facet.dim]: !on})}
              >
                {facet.barLabel ?? facet.label}
              </button>
            );
          })}
          <span className="filterbar-menu">
            <span className="filterbar-separator" aria-hidden="true">·</span>
            <Menu
              label="Order"
              options={sortMenuOptions}
              value={sort}
              badge={sort === "newest" ? undefined : 1}
              onChange={onSortChange}
            />
          </span>
          {activeCount ? (
            <button className="filterbar-clear" type="button" onClick={clearAll}>
              Clear
            </button>
          ) : null}
        </div>

        {pills.length ? (
          <div className="active-filter-pills" aria-label="Active filters">
            {pills.map((pill) => (
              <button
                key={pill.key}
                className="active-filter-pill"
                type="button"
                style={dimensionStyle(pill.dim)}
                aria-label={`Remove ${pill.label} filter`}
                onClick={pill.remove}
              >
                {pill.label} <span aria-hidden="true">×</span>
              </button>
            ))}
          </div>
        ) : null}
      </div>

      <div className="mobile-filter-row mobile-only">
        <div className={`mobile-search-control${searchOpen ? " is-open" : ""}`}>
          {searchOpen ? (
            <>
              <input
                autoFocus
                className="mobile-search"
                placeholder="Search the crate"
                value={query}
                aria-label="Search"
                onBlur={() => {
                  if (!query) closeSearch();
                }}
                onChange={(e) => onQueryChange(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Escape" || e.key === "Enter") closeSearch();
                }}
              />
              <button
                className="mobile-search-close"
                type="button"
                aria-label="Close search"
                title="Close search"
                onPointerDown={(e) => e.preventDefault()}
                onClick={closeSearch}
              >
                <IconClose size={17} />
              </button>
            </>
          ) : (
            <button
              className="mobile-icon-button"
              type="button"
              aria-label="Search"
              title="Search"
              onClick={() => setSearchOpen(true)}
            >
              <IconSearch size={18} />
            </button>
          )}
        </div>
        <button
          className="mobile-icon-button mobile-shuffle-button"
          type="button"
          aria-label="Shuffle"
          title="Shuffle"
          onClick={onShuffle}
        >
          <IconShuffle size={18} />
        </button>
        <button
          className="mobile-icon-button mobile-filter-button"
          type="button"
          aria-label="Filters"
          title="Filters"
          aria-expanded={sheetOpen}
          aria-controls="library-filter-sheet"
          onClick={() => setSheetOpen(true)}
        >
          <IconFilter size={18} />
          {activeCount ? <span className="menu-badge num">{activeCount}</span> : null}
        </button>
      </div>

      {sheetOpen ? (
        <button
          className="sheet-scrim"
          type="button"
          aria-label="Close filters"
          onClick={closeSheet}
        />
      ) : null}
      <aside
        id="library-filter-sheet"
        className={`filter-sheet${sheetOpen ? " is-open" : ""}`}
        aria-hidden={!sheetOpen}
      >
        <button
          className="sheet-close"
          type="button"
          onClick={closeSheet}
          aria-label="Close filters"
          title="Close filters"
        >
          ×
        </button>
        <div className="sheet-body">
          <div className="filter-sheet-head">
            <h2>Filters</h2>
            {activeCount ? (
              <button type="button" onClick={clearAll}>Clear {activeCount}</button>
            ) : null}
          </div>
          <BpmRange
            low={low}
            high={high}
            min={bpmFloor}
            max={bpmCeiling}
            histogram={histogram}
            onChange={onBpmChange}
          />
          <FacetChips filters={filters} onChange={onChange} ctx={ctx} />
          <section className="filter-sheet-section sheet-sorts">
            <h3 className="fgroup-label">Sort</h3>
            <div className="sheet-chips">
              {sortOptions.map((option) => (
                <button
                  key={option.key}
                  className={`chip${sort === option.asc || sort === option.desc ? " is-on" : ""}`}
                  type="button"
                  onClick={() => onSortChange(nextSort(sort, option))}
                >
                  {option.label}
                  {option.fixed
                    ? ""
                    : sort === option.desc
                      ? " ▼"
                      : sort === option.asc
                        ? " ▲"
                        : ""}
                </button>
              ))}
            </div>
          </section>
        </div>
      </aside>
    </>
  );
}

export function filtersToQuery(filters: Filters): string {
  const params = new URLSearchParams();
  for (const facet of FACETS) {
    if (facet.kind === "single") {
      const value = filters[facet.dim];
      if (value) params.set(facet.param, value);
    } else if (facet.kind === "multi") {
      for (const value of filters[facet.dim]) params.append(facet.param, value);
    } else if (filters[facet.dim]) {
      params.set(facet.param, "true");
    }
  }
  return params.toString();
}
