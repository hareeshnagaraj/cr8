/** Dismiss keyboard / drop sticky iOS zoom after closing a field or sheet. */
export function releaseFocus(): void {
  const active = document.activeElement;
  if (active instanceof HTMLElement) active.blur();
  // iOS sometimes keeps a residual zoom after blur; a no-op scroll nudges it.
  window.scrollTo(window.scrollX, window.scrollY);
}
