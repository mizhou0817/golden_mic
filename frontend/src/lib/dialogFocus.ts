import type { KeyboardEvent } from 'react';

/** Keep ordinary Tab inside the current native modal, including changing controls. */
export function containDialogTab(event: KeyboardEvent<HTMLDialogElement>) {
  const dialog = event.currentTarget;
  if (event.key !== 'Tab' || event.defaultPrevented || event.ctrlKey || event.altKey || event.metaKey
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