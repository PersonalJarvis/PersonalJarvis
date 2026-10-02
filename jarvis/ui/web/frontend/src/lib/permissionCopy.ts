/**
 * Copy for the permission surfaces: one FULL sentence per (feature, reason),
 * from i18n, never assembled from fragments and never the backend's English.
 *
 * Why per pair: German and Spanish agree verbs and articles with the subject, so
 * "{feature} + {reason}" glued from pieces breaks their grammar. Each sentence
 * in `permissions.prompt.<feature>.<reason>` is written whole. Only two TOKENS
 * are substituted into it: `{app}` (the name macOS shows, from
 * `app_identity.app_name`) and `{permissions}` (the localised, quoted names of
 * the permissions still missing, joined as a list).
 *
 * A feature this UI does not know (a newer backend) falls back to
 * `permissions.prompt.generic.<reason>`, so nothing renders as a bare key.
 *
 * One exception to "a sentence per (feature, reason)": outside the installed app
 * the only way forward is the "Allow for the app that started {app}" button, and
 * nothing has been asked yet. The per-feature sentences say "Continue and macOS
 * will ask you" or "switch it on in System Settings", which contradict that
 * button, so for that case (see {@link isOutsideAskEpisode}) the card and the
 * inline notes use ONE dedicated sentence and heading instead of fourteen
 * features' worth of variants.
 *
 * `permissionCopy.test.ts` asserts every `PERMISSION_FEATURES` x
 * `PERMISSION_NEEDED_REASONS` pair has copy in all three locales.
 */
import { fill } from "@/i18n";
import { PERMISSION_FEATURES } from "./permissionEvents";
import type { PromptEpisode } from "./permissionPrompts";

/** Used only when the backend sent no `app_identity.app_name`. */
export const FALLBACK_APP_NAME = "Personal Jarvis";

/** The i18n key of the sentence for one episode. */
export function promptCopyKey(feature: string, reason: string): string {
  const known = (PERMISSION_FEATURES as readonly string[]).includes(feature);
  return `permissions.prompt.${known ? feature : "generic"}.${reason}`;
}

type Translate = (key: string) => string;

/** What decides the wording of an episode. */
type CopyEpisode = Pick<PromptEpisode, "feature" | "reason" | "permissions"> &
  Partial<Pick<PromptEpisode, "outside_app" | "can_prompt">>;

/**
 * The episode's way forward is the outside-app confirmation: nothing was asked
 * yet, and "Allow for the app that started {app}" is the ask. Mirrors the
 * `allow_outside` rows of `promptActions` for the two reasons whose per-feature
 * sentence would promise something else (`not_determined`: macOS asks next,
 * `needs_settings`: switch it on in System Settings). Any other reason keeps its
 * own sentence (denied is still true outside the installed app).
 */
export function isOutsideAskEpisode(
  episode: Pick<CopyEpisode, "reason" | "outside_app" | "can_prompt">,
): boolean {
  return (
    episode.outside_app === true &&
    episode.can_prompt === true &&
    (episode.reason === "not_determined" || episode.reason === "needs_settings")
  );
}

/** The i18n key of the card's heading for one episode. */
export function promptHeadingKey(
  episode: Pick<CopyEpisode, "reason" | "outside_app" | "can_prompt">,
): string {
  return `permissions.prompt.heading.${isOutsideAskEpisode(episode) ? "outside_app" : episode.reason}`;
}

/** "“Microphone” and “Accessibility”": localised names, quoted, joined as a list. */
export function listPermissionNames(
  t: Translate,
  permissions: readonly string[],
  language: string,
): string {
  const names = permissions.map((id) => fill(t("permissions.prompt.name"), { 0: t(`permissions.items.${id}.title`) }));
  if (names.length <= 1) return names[0] ?? "";
  const ListFormat = (Intl as unknown as {
    ListFormat?: new (
      locale: string,
      options: { style: string; type: string },
    ) => { format: (items: string[]) => string };
  }).ListFormat;
  if (ListFormat) {
    try {
      return new ListFormat(language, { style: "long", type: "conjunction" }).format(names);
    } catch {
      // An engine without this locale's list data: the plain list below is correct enough.
    }
  }
  return names.join(", ");
}

/**
 * The full sentence the card (and an inline note) shows for an episode. Outside
 * the installed app it is the one dedicated `permissions.prompt.outside_sentence`
 * (it already names the grantee), otherwise the per-(feature, reason) sentence.
 */
export function promptSentence(input: {
  t: Translate;
  language: string;
  episode: CopyEpisode;
  appName: string;
  /** The permissions to name; defaults to all of the episode's. */
  missing?: readonly string[];
}): string {
  const { t, language, episode, appName, missing } = input;
  const key = isOutsideAskEpisode(episode)
    ? "permissions.prompt.outside_sentence"
    : promptCopyKey(episode.feature, episode.reason);
  return fill(t(key), {
    app: appName || FALLBACK_APP_NAME,
    permissions: listPermissionNames(t, missing ?? episode.permissions, language),
  });
}
