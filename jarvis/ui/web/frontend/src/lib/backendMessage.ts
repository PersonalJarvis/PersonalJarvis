import { fill } from "@/i18n";

/** Fill-in values a backend sends next to a message code. */
export type BackendMessageParams = Record<string, string | number> | null | undefined;

/**
 * Render a backend status sentence in the UI language.
 *
 * Routes that show prose to the user return a stable `code` (plus `params`)
 * next to their English sentence. The translation lives under
 * `<namespace>.<code>`; an empty code, or one this build has no translation
 * for (a newer backend), shows the backend's own sentence unchanged.
 */
export function backendMessage(
  t: (key: string) => string,
  namespace: string,
  code: string | null | undefined,
  params: BackendMessageParams,
  fallback: string,
): string {
  if (!code) return fallback;
  const key = `${namespace}.${code}`;
  const template = t(key);
  if (template === key) return fallback;
  return fill(template, params ?? {});
}
