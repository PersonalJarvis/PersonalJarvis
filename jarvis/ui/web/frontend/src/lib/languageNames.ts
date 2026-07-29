/**
 * Human-readable names for the language codes the recogniser accepts.
 *
 * The backend hands out plain codes ("de", "yue", "auto"). Showing those raw is
 * a usability wall for the exact people the wide language list is FOR — someone
 * looking for their own language should not have to know its ISO code.
 *
 * `Intl.DisplayNames` is the browser's own translated language table, so the
 * list reads in the user's interface language without shipping ~100 names × 3
 * locales of our own. It covers the standard two-letter codes; the few
 * recogniser-specific spellings that predate ISO-639-1 (`jw`, `yue`, `haw`) get
 * an explicit entry below, because `Intl` either misses them or answers with the
 * code itself.
 */

/** Codes `Intl.DisplayNames` does not resolve, mapped to the code it does. */
const CODE_ALIASES: Readonly<Record<string, string>> = {
  // The recogniser's historical spelling of Javanese (ISO-639-1 is "jv").
  jw: "jv",
  // Cantonese: a valid BCP-47 subtag that older Intl data may not carry.
  yue: "yue",
  haw: "haw",
};

/** Last-resort names for codes no Intl implementation resolves. */
const FALLBACK_NAMES: Readonly<Record<string, string>> = {
  jw: "Javanese",
  jv: "Javanese",
  yue: "Cantonese",
  haw: "Hawaiian",
  ln: "Lingala",
  ba: "Bashkir",
  br: "Breton",
  tk: "Turkmen",
  tt: "Tatar",
  sa: "Sanskrit",
  oc: "Occitan",
  nn: "Norwegian Nynorsk",
  as: "Assamese",
  bo: "Tibetan",
  mi: "Maori",
  sn: "Shona",
};

/**
 * The languages most people on earth speak, most-spoken first.
 *
 * Ordered by total speakers worldwide — an outside fact about the planet, not
 * about whoever happens to maintain this repo (§3: the maintainer's own
 * languages are never the baseline). It exists because scrolling an
 * alphabetical ~100-row list past Afrikaans, Albanian, Amharic and Armenian to
 * reach English is a worse first screen than showing the handful of entries a
 * random downloader is most likely to want.
 *
 * It only ever REORDERS: every code the backend serves stays reachable in the
 * full list below, and the user's own interface language is put ahead of this
 * ranking at build time, so a Hungarian UI leads with Hungarian.
 */
export const COMMON_LANGUAGE_CODES: readonly string[] = [
  "en",
  "zh",
  "hi",
  "es",
  "ar",
  "fr",
  "bn",
  "pt",
  "ru",
  "ur",
  "id",
  "de",
];

// One instance per UI language: constructing an Intl formatter is not free, and
// this runs once per option in a ~100-row list.
const displayNamesCache = new Map<string, Intl.DisplayNames | null>();

function displayNamesFor(uiLanguage: string): Intl.DisplayNames | null {
  const cached = displayNamesCache.get(uiLanguage);
  if (cached !== undefined) return cached;
  let instance: Intl.DisplayNames | null = null;
  try {
    instance = new Intl.DisplayNames([uiLanguage], { type: "language" });
  } catch {
    // No Intl.DisplayNames (very old runtime, or an unsupported locale): the
    // fallback table and the raw code still produce something readable.
    instance = null;
  }
  displayNamesCache.set(uiLanguage, instance);
  return instance;
}

/**
 * The name to show for one recognition-language code.
 *
 * `autoLabel` is passed in rather than looked up here so the caller keeps
 * ownership of its own translated "Automatic" wording.
 */
export function languageName(
  code: string,
  uiLanguage: string,
  autoLabel?: string,
): string {
  if (code === "auto") return autoLabel ?? "Automatic";
  const resolved = CODE_ALIASES[code] ?? code;
  const intl = displayNamesFor(uiLanguage);
  if (intl) {
    try {
      const name = intl.of(resolved);
      // Intl answers with the input when it has no name for it — that is not a
      // translation, so fall through to our own table instead of showing a code.
      if (name && name.toLowerCase() !== resolved.toLowerCase()) return name;
    } catch {
      /* invalid subtag — fall through */
    }
  }
  return FALLBACK_NAMES[code] ?? FALLBACK_NAMES[resolved] ?? code;
}

/**
 * What speakers of a language call it themselves ("Deutsch", "中文", "العربية").
 *
 * Shown next to the translated name because the translated name is only useful
 * to someone who already reads the interface language. A Japanese speaker
 * handed an English UI scans for 日本語, not for "Japanese" — and this is the
 * one list in the app where that person is the expected visitor.
 *
 * Returns an empty string when the endonym adds nothing (same as the displayed
 * name, or unresolvable), so callers can simply skip rendering it.
 */
export function languageEndonym(code: string, uiLanguage: string): string {
  if (code === "auto") return "";
  const resolved = CODE_ALIASES[code] ?? code;
  const own = displayNamesFor(resolved);
  if (!own) return "";
  let name: string | undefined;
  try {
    name = own.of(resolved);
  } catch {
    return "";
  }
  if (!name || name.toLowerCase() === resolved.toLowerCase()) return "";
  return name === languageName(code, uiLanguage) ? "" : name;
}

/** Lower-cased and stripped of accents, so "francais" finds "Français". */
function foldForSearch(value: string): string {
  return value
    .toLocaleLowerCase()
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "");
}

/**
 * Everything a user might type to find one language: its name in the interface
 * language, its own name, its English name, and its code.
 *
 * The English name is in there because half the world's technical vocabulary is
 * English — someone running a German UI may well type "japanese".
 */
export function languageSearchText(code: string, uiLanguage: string): string {
  return foldForSearch(
    [
      languageName(code, uiLanguage),
      languageEndonym(code, uiLanguage),
      languageName(code, "en"),
      code,
    ]
      .filter(Boolean)
      .join(" "),
  );
}

/** True when `query` matches this language by name, own name, or code. */
export function languageMatches(
  code: string,
  uiLanguage: string,
  query: string,
): boolean {
  const needle = foldForSearch(query).trim();
  if (!needle) return true;
  const haystack = languageSearchText(code, uiLanguage);
  return needle.split(/\s+/).every((word) => haystack.includes(word));
}

/**
 * Options sorted for display: "auto" first (it is the recommended setting, not a
 * language), then every language by its localized name in the user's own
 * collation order.
 */
export function sortedLanguageOptions(
  codes: readonly string[],
  uiLanguage: string,
  autoLabel?: string,
): { code: string; label: string }[] {
  const collator = new Intl.Collator(uiLanguage);
  const auto = codes.filter((c) => c === "auto");
  const rest = codes
    .filter((c) => c !== "auto")
    .map((code) => ({ code, label: languageName(code, uiLanguage) }))
    .sort((a, b) => collator.compare(a.label, b.label));
  return [
    ...auto.map((code) => ({ code, label: languageName(code, uiLanguage, autoLabel) })),
    ...rest,
  ];
}

export interface LanguageOption {
  code: string;
  label: string;
  /** The language's own name, or "" when it would just repeat `label`. */
  endonym: string;
}

export interface LanguageOptionGroup {
  /** `""` for the ungrouped lead entry ("auto"), otherwise a group id. */
  id: "" | "common" | "all";
  options: LanguageOption[];
}

/**
 * The same options as {@link sortedLanguageOptions}, split into the three bands
 * the picker draws: the lead entry ("auto"), a short most-likely band, and the
 * complete alphabetical list.
 *
 * `preferred` is the user's interface language — it heads the common band, so
 * the shortlist is about the person using the app, not about a ranking baked
 * into the source. `keepInCommon` (the currently stored value) is pulled up too,
 * so a saved choice is never a scroll away from where it was picked.
 *
 * Nothing is hidden: every language appears in the `all` band as well, which is
 * what makes the shortlist a convenience rather than a two-tier list.
 */
export function groupedLanguageOptions(
  codes: readonly string[],
  uiLanguage: string,
  autoLabel?: string,
  options: { preferred?: string; keepInCommon?: string } = {},
): LanguageOptionGroup[] {
  const { preferred, keepInCommon } = options;
  const collator = new Intl.Collator(uiLanguage);
  const served = new Set(codes);
  const decorate = (code: string, label?: string): LanguageOption => ({
    code,
    label: label ?? languageName(code, uiLanguage),
    endonym: languageEndonym(code, uiLanguage),
  });

  const lead = codes
    .filter((c) => c === "auto")
    .map((code) => decorate(code, languageName(code, uiLanguage, autoLabel)));

  // Order inside the shortlist: the interface language, then the saved pick,
  // then the world ranking — de-duplicated, and never a code the backend does
  // not actually accept.
  const commonCodes: string[] = [];
  for (const code of [preferred, keepInCommon, ...COMMON_LANGUAGE_CODES]) {
    if (!code || code === "auto") continue;
    if (!served.has(code)) continue;
    if (commonCodes.includes(code)) continue;
    commonCodes.push(code);
  }

  const all = codes
    .filter((c) => c !== "auto")
    .map((code) => decorate(code))
    .sort((a, b) => collator.compare(a.label, b.label));

  const groups: LanguageOptionGroup[] = [];
  if (lead.length) groups.push({ id: "", options: lead });
  // A shortlist that is most of the list is not a shortlist — with a handful of
  // languages served (a test fixture, a trimmed backend) the alphabetical band
  // alone is clearer than two bands showing nearly the same rows.
  if (commonCodes.length && all.length > commonCodes.length + 3) {
    groups.push({ id: "common", options: commonCodes.map((code) => decorate(code)) });
  }
  if (all.length) groups.push({ id: "all", options: all });
  return groups;
}
