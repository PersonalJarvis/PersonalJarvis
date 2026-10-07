/**
 * Remembered acknowledgement of an experimental provider route.
 *
 * The notice is worth showing once: it explains whose plan pays and that the
 * route can change without notice. Showing it on EVERY switch is the
 * confirmation fatigue this project rejects, and it taught the user to click
 * it away unread, which defeats the point of having it.
 *
 * A per-machine flag in localStorage, keyed per provider. A WebView with
 * storage disabled simply asks again next time: annoying, never broken, and
 * never silently skipping the notice.
 */

function consentKey(providerId: string): string {
  return `jarvis.experimentalConsent.${providerId}`;
}

export function hasExperimentalConsent(providerId: string): boolean {
  try {
    return window.localStorage.getItem(consentKey(providerId)) === "1";
  } catch {
    // Storage unavailable: ask again rather than assume consent.
    return false;
  }
}

export function rememberExperimentalConsent(providerId: string): void {
  try {
    window.localStorage.setItem(consentKey(providerId), "1");
  } catch {
    // Storage unavailable: the dialog reappears next time, nothing else breaks.
  }
}
