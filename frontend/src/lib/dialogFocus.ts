import type { KeyboardEvent } from 'react';

/** Keep ordinary Tab inside the current native modal, including changing controls. */
export function containDialogTab(event: KeyboardEvent<HTMLDialogElement>) {
  const dialog = event.currentTarget;
  // macOS Safari moves between buttons/links only with Option+Tab, so that must stay inside the dialog too;
  // elsewhere Alt+Tab belongs to the operating system.
  const optionTab = event.altKey && typeof navigator !== 'undefined' && /^(Mac|iPhone|iPad)/.test(navigator.platform);
  if (event.key !== 'Tab' || event.defaultPrevented || event.ctrlKey || (event.altKey && !optionTab) || event.metaKey
      || !dialog.open || !dialog.matches(':modal')) return;
  const available = Array.from(dialog.querySelectorAll<HTMLElement>('input, button, select, textarea, a[href], summary, [tabindex]'))
    .filter(element => element.tabIndex >= 0 && !element.matches(':disabled') && !element.closest('[inert]')
      && element.checkVisibility({ visibilityProperty: true }) && element.getClientRects().length > 0);
  const stops = available.filter(element => {
    const radio = element as HTMLInputElement;
    if (radio.type !== 'radio' || !radio.name) return true;
    const group = available.filter(candidate => {
      const other = candidate as HTMLInputElement;
      return other.type === 'radio' && other.name === radio.name && other.form === radio.form;
    });
    return element === (group.find(candidate => (candidate as HTMLInputElement).checked) ?? group[0]);
  });
  const index = stops.indexOf(dialog.ownerDocument.activeElement as HTMLElement);
  if (!stops.length || index < 0 || (event.shiftKey ? index === 0 : index === stops.length - 1)) {
    event.preventDefault();
    (event.shiftKey ? stops[stops.length - 1] ?? dialog : stops[0] ?? dialog).focus({ preventScroll: true });
  }
}

/** Where focus goes back when a dialog closes: the control the user actually activated to open it (recorded on
 * click). Safari does not focus a clicked button, so the active element is then <body> or the main region, not the
 * button. Without a recorded opener (e.g. opened by the app itself) the active element is used. */
export function focusReturnTarget(opener: HTMLElement | null): HTMLElement | null {
  if (opener?.isConnected) return opener;
  const active = typeof document === 'undefined' ? null : document.activeElement;
  return active instanceof HTMLElement && active !== document.body ? active : null;
}
