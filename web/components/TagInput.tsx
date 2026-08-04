"use client";

import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type FocusEvent,
  type KeyboardEvent,
} from "react";

const TAG_DIMENSIONS = [
  {name: "vibe", label: "VIBE", createLabel: "vibe"},
  {name: "instr", label: "INSTRUMENTS", createLabel: "instr"},
  {name: "collab", label: "COLLAB", createLabel: "collab"},
  {name: "use", label: "USE", createLabel: "use"},
] as const;

type TagDimension = (typeof TAG_DIMENSIONS)[number]["name"];
type VocabularyValue = {value: string; count: number};
type TagPanel = {
  dimensions?: {name: string; allow_new?: boolean}[];
  vocab_options?: Record<string, VocabularyValue[]>;
};

type ResultItem = {
  id: string;
  kind: "value";
  dim: TagDimension;
  value: string;
  count: number;
};

type CreateItem = {
  id: string;
  kind: "create";
  dim: TagDimension;
  value: string;
};

type SelectableItem = ResultItem | CreateItem;

export function TagInput({
  sourceSongUlid,
  onApply,
  onEscape,
  autoFocus = false,
  placeholder = "Add a tag…",
  ariaLabel = "Add a tag",
  className = "",
}: {
  sourceSongUlid: string;
  onApply: (
    dim: TagDimension,
    value: string,
  ) => boolean | void | Promise<boolean | void>;
  onEscape?: () => void;
  autoFocus?: boolean;
  placeholder?: string;
  ariaLabel?: string;
  className?: string;
}) {
  const [query, setQuery] = useState("");
  const [panel, setPanel] = useState<TagPanel | null>(null);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);
  const [highlighted, setHighlighted] = useState<string | null>(null);
  const [applying, setApplying] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const optionRefs = useRef(new Map<string, HTMLButtonElement>());
  const loadedSource = useRef<string | null>(null);
  const loadingSource = useRef<string | null>(null);
  const currentSource = useRef(sourceSongUlid);
  const previousSource = useRef(sourceSongUlid);
  const listboxId = useId();

  currentSource.current = sourceSongUlid;

  useEffect(() => {
    if (previousSource.current === sourceSongUlid) return;
    previousSource.current = sourceSongUlid;
    loadedSource.current = null;
    setPanel(null);
    setQuery("");
    setOpen(false);
    setLoadFailed(false);
  }, [sourceSongUlid]);

  async function loadVocabulary() {
    if (
      !sourceSongUlid ||
      loadedSource.current === sourceSongUlid ||
      loadingSource.current === sourceSongUlid
    ) {
      return;
    }

    const requestedSource = sourceSongUlid;
    loadingSource.current = requestedSource;
    setLoading(true);
    setLoadFailed(false);
    try {
      const response = await fetch(`/api/songs/${requestedSource}`, {
        credentials: "same-origin",
      });
      if (!response.ok) throw new Error(`tag vocabulary failed: ${response.status}`);
      const nextPanel = (await response.json()) as TagPanel;
      if (currentSource.current !== requestedSource) return;
      loadedSource.current = requestedSource;
      setPanel(nextPanel);
    } catch {
      if (currentSource.current === requestedSource) setLoadFailed(true);
    } finally {
      if (loadingSource.current === requestedSource) {
        loadingSource.current = null;
        setLoading(false);
      }
    }
  }

  const normalizedQuery = query.trim().toLocaleLowerCase();
  const allowedNew = useMemo(() => {
    const byName = new Map(
      (panel?.dimensions ?? []).map((dimension) => [dimension.name, dimension]),
    );
    return TAG_DIMENSIONS.filter(({name}) => byName.get(name)?.allow_new);
  }, [panel]);

  const groups = useMemo(() => {
    if (!normalizedQuery) return [];
    return TAG_DIMENSIONS.map((dimension) => {
      const values = (panel?.vocab_options?.[dimension.name] ?? [])
        .map((option, sourceIndex) => ({
          ...option,
          sourceIndex,
          prefix: option.value.toLocaleLowerCase().startsWith(normalizedQuery),
        }))
        .filter((option) =>
          option.value.toLocaleLowerCase().includes(normalizedQuery),
        )
        .sort((left, right) => {
          if (left.prefix !== right.prefix) return left.prefix ? -1 : 1;
          if (left.count !== right.count) return right.count - left.count;
          return left.sourceIndex - right.sourceIndex;
        });
      return {...dimension, values};
    }).filter((group) => group.values.length);
  }, [normalizedQuery, panel]);

  const exactMatch = groups.some((group) =>
    group.values.some(
      (option) => option.value.trim().toLocaleLowerCase() === normalizedQuery,
    ),
  );

  const resultItems: ResultItem[] = groups.flatMap((group) =>
    group.values.map((option) => ({
      id: `value:${group.name}:${option.value}`,
      kind: "value" as const,
      dim: group.name,
      value: option.value,
      count: option.count,
    })),
  );
  const createItems: CreateItem[] = normalizedQuery && !exactMatch
    ? allowedNew.map((dimension) => ({
        id: `create:${dimension.name}`,
        kind: "create" as const,
        dim: dimension.name,
        value: query.trim(),
      }))
    : [];
  const selectableItems: SelectableItem[] = [...resultItems, ...createItems];
  const selectableKey = selectableItems.map((item) => item.id).join("\u0000");
  const defaultHighlight = createItems[0]?.id ?? resultItems[0]?.id ?? null;

  useEffect(() => {
    setHighlighted(defaultHighlight);
  }, [normalizedQuery, defaultHighlight]);

  useEffect(() => {
    if (!highlighted) return;
    optionRefs.current.get(highlighted)?.scrollIntoView({block: "nearest"});
  }, [highlighted, selectableKey]);

  async function apply(item: SelectableItem) {
    if (applying) return;
    setApplying(true);
    try {
      const applied = await onApply(item.dim, item.value);
      if (applied === false) return;
      setQuery("");
      setOpen(false);
    } catch {
      // The caller owns the visible write feedback; leaving the query in place
      // makes the failed value immediately retryable.
    } finally {
      setApplying(false);
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }

  function moveHighlight(direction: 1 | -1) {
    if (!selectableItems.length) return;
    setHighlighted((current) => {
      const currentIndex = selectableItems.findIndex((item) => item.id === current);
      const nextIndex = currentIndex < 0
        ? direction > 0 ? 0 : selectableItems.length - 1
        : (currentIndex + direction + selectableItems.length) % selectableItems.length;
      return selectableItems[nextIndex].id;
    });
  }

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      setOpen(false);
      onEscape?.();
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      setOpen(true);
      moveHighlight(event.key === "ArrowDown" ? 1 : -1);
      return;
    }
    if (
      event.key === "Enter" &&
      event.target === inputRef.current &&
      open &&
      highlighted
    ) {
      const item = selectableItems.find((candidate) => candidate.id === highlighted);
      if (!item) return;
      event.preventDefault();
      void apply(item);
    }
  }

  function handleBlur(event: FocusEvent<HTMLDivElement>) {
    if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
    setOpen(false);
  }

  const showDropdown = open && Boolean(normalizedQuery);
  const domId = (itemId: string) => `${listboxId}-${encodeURIComponent(itemId)}`;

  return (
    <div
      className={`tag-input${className ? ` ${className}` : ""}`}
      onKeyDown={handleKeyDown}
      onBlur={handleBlur}
    >
      <input
        ref={inputRef}
        className="input tag-input-field"
        value={query}
        placeholder={placeholder}
        aria-label={ariaLabel}
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={showDropdown}
        aria-controls={listboxId}
        aria-activedescendant={highlighted ? domId(highlighted) : undefined}
        autoComplete="off"
        autoFocus={autoFocus}
        disabled={applying}
        onFocus={() => {
          void loadVocabulary();
          if (normalizedQuery) setOpen(true);
        }}
        onChange={(event) => {
          setQuery(event.target.value);
          setOpen(true);
          void loadVocabulary();
        }}
      />

      {showDropdown ? (
        <div className="tag-dropdown" id={listboxId} role="listbox">
          {loading ? (
            <p className="tag-dropdown-status" role="status">Loading tags…</p>
          ) : loadFailed ? (
            <p className="tag-dropdown-status is-error" role="alert">
              Couldn’t load tags.
            </p>
          ) : (
            <>
              {groups.map((group) => (
                <div className="tag-result-group" role="group" aria-label={group.label} key={group.name}>
                  <p className="tag-result-heading">{group.label}</p>
                  {group.values.map((option) => {
                    const item = resultItems.find(
                      (candidate) =>
                        candidate.dim === group.name && candidate.value === option.value,
                    );
                    if (!item) return null;
                    return (
                      <button
                        ref={(node) => {
                          if (node) optionRefs.current.set(item.id, node);
                          else optionRefs.current.delete(item.id);
                        }}
                        id={domId(item.id)}
                        className={`tag-result${highlighted === item.id ? " is-highlighted" : ""}`}
                        type="button"
                        role="option"
                        aria-selected={highlighted === item.id}
                        tabIndex={-1}
                        key={item.id}
                        onMouseEnter={() => setHighlighted(item.id)}
                        onClick={() => void apply(item)}
                      >
                        <span>{option.value}</span>
                        <span className="tag-result-count num">{option.count}</span>
                      </button>
                    );
                  })}
                </div>
              ))}

              {createItems.length ? (
                <div className="tag-create-row">
                  <span>create “{query.trim()}” in —</span>
                  <span className="tag-create-dimensions">
                    {createItems.map((item, index) => {
                      const dimension = TAG_DIMENSIONS.find(({name}) => name === item.dim);
                      return (
                        <span className="tag-create-choice" key={item.id}>
                          {index ? <span aria-hidden="true"> · </span> : null}
                          <button
                            ref={(node) => {
                              if (node) optionRefs.current.set(item.id, node);
                              else optionRefs.current.delete(item.id);
                            }}
                            id={domId(item.id)}
                            className={highlighted === item.id ? "is-highlighted" : ""}
                            type="button"
                            role="option"
                            aria-selected={highlighted === item.id}
                            onFocus={() => setHighlighted(item.id)}
                            onMouseEnter={() => setHighlighted(item.id)}
                            onClick={() => void apply(item)}
                          >
                            {dimension?.createLabel}
                          </button>
                        </span>
                      );
                    })}
                  </span>
                </div>
              ) : null}

              {!resultItems.length && !createItems.length ? (
                <p className="tag-dropdown-status">No matching tags.</p>
              ) : null}
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}
