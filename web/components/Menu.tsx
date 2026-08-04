"use client";

import {
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";

export type MenuOption = {
  value: string;
  label?: string;
  count?: number;
  swatch?: string;
};

export type MenuProps<Value extends string | string[]> = {
  label: string | ReactNode;
  options: MenuOption[];
  value: Value;
  onChange: (value: Value) => void;
  searchable?: boolean;
  badge?: number;
  activeColor?: string | null;
  activeWash?: string | null;
};

export function Menu<Value extends string | string[]>({
  label,
  options,
  value,
  onChange,
  searchable = false,
  badge,
  activeColor,
  activeWash,
}: MenuProps<Value>) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(-1);
  const [flipped, setFlipped] = useState(false);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const optionRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const listId = useId();
  const multi = Array.isArray(value);
  const showSearch = searchable && options.length > 8;

  const filteredOptions = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    if (!needle) return options;
    return options.filter((option) =>
      `${option.label ?? ""} ${option.value}`
        .toLocaleLowerCase()
        .includes(needle),
    );
  }, [options, query]);

  function isSelected(option: MenuOption) {
    return multi ? value.includes(option.value) : value === option.value;
  }

  function openMenu(preferred: "first" | "last" | "selected" = "selected") {
    setQuery("");
    setFlipped(false);
    if (preferred === "first") {
      setActiveIndex(options.length ? 0 : -1);
    } else if (preferred === "last") {
      setActiveIndex(options.length - 1);
    } else {
      const selected = options.findIndex(isSelected);
      setActiveIndex(selected >= 0 ? selected : options.length ? 0 : -1);
    }
    setOpen(true);
  }

  function closeMenu(restoreFocus = false) {
    setOpen(false);
    setQuery("");
    if (restoreFocus) requestAnimationFrame(() => triggerRef.current?.focus());
  }

  function selectOption(option: MenuOption) {
    if (multi) {
      const selected = value as string[];
      const next = selected.includes(option.value)
        ? selected.filter((item) => item !== option.value)
        : [...selected, option.value];
      onChange(next as Value);
      return;
    }
    onChange(option.value as Value);
    closeMenu(true);
  }

  function moveActive(next: number) {
    if (!filteredOptions.length) return;
    const index = (next + filteredOptions.length) % filteredOptions.length;
    setActiveIndex(index);
  }

  function handleKeyDown(event: ReactKeyboardEvent) {
    if (!open) {
      if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
        event.preventDefault();
        openMenu(event.key === "ArrowUp" || event.key === "End" ? "last" : "first");
      }
      return;
    }

    if (event.key === "ArrowDown") {
      event.preventDefault();
      moveActive(activeIndex + 1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      moveActive(activeIndex - 1);
    } else if (event.key === "Home") {
      event.preventDefault();
      setActiveIndex(filteredOptions.length ? 0 : -1);
    } else if (event.key === "End") {
      event.preventDefault();
      setActiveIndex(filteredOptions.length - 1);
    } else if (event.key === "Enter" && activeIndex >= 0) {
      event.preventDefault();
      selectOption(filteredOptions[activeIndex]);
    } else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      closeMenu(true);
    } else if (event.key === "Tab") {
      closeMenu();
    }
  }

  useEffect(() => {
    if (!open) return;
    const handlePointerDown = (event: PointerEvent) => {
      if (!wrapperRef.current?.contains(event.target as Node)) closeMenu();
    };
    document.addEventListener("pointerdown", handlePointerDown);
    return () => document.removeEventListener("pointerdown", handlePointerDown);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    requestAnimationFrame(() => {
      if (showSearch) searchRef.current?.focus();
      else listRef.current?.focus();
    });
  }, [open, showSearch]);

  useEffect(() => {
    if (!open) return;
    setActiveIndex((current) =>
      filteredOptions.length
        ? Math.min(Math.max(current, 0), filteredOptions.length - 1)
        : -1,
    );
  }, [filteredOptions.length, open]);

  useEffect(() => {
    if (!open || activeIndex < 0) return;
    optionRefs.current[activeIndex]?.scrollIntoView({block: "nearest"});
  }, [activeIndex, open]);

  useLayoutEffect(() => {
    if (!open || !panelRef.current) return;
    setFlipped(panelRef.current.getBoundingClientRect().bottom > window.innerHeight);
  }, [filteredOptions.length, open, showSearch]);

  const activeId = activeIndex >= 0 ? `${listId}-option-${activeIndex}` : undefined;
  const active = badge !== undefined && badge > 0;
  const menuStyle = {
    ["--menu-active" as string]: activeColor ?? "var(--accent)",
    ["--menu-wash" as string]:
      activeWash ?? "rgba(255, 255, 255, 0.09)",
  } as CSSProperties;

  return (
    <div
      className="menu"
      ref={wrapperRef}
      style={menuStyle}
      onKeyDown={handleKeyDown}
    >
      <button
        ref={triggerRef}
        className={`menu-trigger${active ? " is-active" : ""}`}
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listId}
        onClick={() => (open ? closeMenu() : openMenu())}
      >
        <span className="menu-trigger-label">{label}</span>
        {badge !== undefined ? <span className="menu-badge num">{badge}</span> : null}
        <span className="menu-chevron" aria-hidden="true">⌄</span>
      </button>

      {open ? (
        <div
          ref={panelRef}
          className={`menu-panel${flipped ? " is-flipped" : ""}`}
        >
          {showSearch ? (
            <input
              ref={searchRef}
              className="menu-search"
              type="search"
              value={query}
              placeholder="Search"
              aria-label="Search options"
              aria-controls={listId}
              aria-activedescendant={activeId}
              autoComplete="off"
              onChange={(event) => {
                setQuery(event.target.value);
                setActiveIndex(0);
              }}
            />
          ) : null}
          <div
            ref={listRef}
            id={listId}
            className="menu-list"
            role="listbox"
            tabIndex={-1}
            aria-multiselectable={multi || undefined}
            aria-activedescendant={activeId}
          >
            {filteredOptions.length ? (
              filteredOptions.map((option, index) => {
                const selected = isSelected(option);
                return (
                  <button
                    key={option.value}
                    ref={(node) => {
                      optionRefs.current[index] = node;
                    }}
                    id={`${listId}-option-${index}`}
                    className={`menu-option${selected ? " is-selected" : ""}${
                      index === activeIndex ? " is-active" : ""
                    }`}
                    type="button"
                    role="option"
                    tabIndex={-1}
                    aria-selected={selected}
                    onPointerMove={() => setActiveIndex(index)}
                    onMouseDown={(event) => event.preventDefault()}
                    onClick={() => selectOption(option)}
                  >
                    <span className="menu-check" aria-hidden="true">
                      {selected ? "✓" : ""}
                    </span>
                    {option.swatch ? (
                      <span
                        className="menu-swatch"
                        style={{backgroundColor: option.swatch}}
                        aria-hidden="true"
                      />
                    ) : null}
                    <span className="menu-option-label">
                      {option.label ?? option.value}
                    </span>
                    {option.count !== undefined ? (
                      <span className="menu-option-count num">{option.count}</span>
                    ) : null}
                  </button>
                );
              })
            ) : (
              <div className="menu-empty">No matches</div>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}
