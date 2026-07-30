import { useCallback, useMemo } from "react";

import { Combobox, type ComboboxGroup, type ComboboxOption } from "@/components/ui/combobox";
import { useT, useUiLanguage } from "@/i18n";
import {
  groupedLanguageOptions,
  languageMatches,
  languageName,
} from "@/lib/languageNames";

export interface LanguageSelectProps {
  /** The stored code, e.g. "auto", "de", "yue". */
  value: string;
  /** Every code the backend accepts — never a copy kept in the frontend. */
  codes: readonly string[];
  onChange: (code: string) => void;
  /** Wording for the "detect automatically" entry, owned by the caller. */
  autoLabel: string;
  ariaLabel: string;
  disabled?: boolean;
  className?: string;
  testId?: string;
}

/**
 * The recognition-language picker: one searchable list with a short band of
 * common languages on top and the full alphabetical list below.
 *
 * The recogniser accepts around a hundred languages, and both places that let
 * someone choose one had a native `<select>`, where the list arrives as an
 * unstyled operating-system widget with no search — so finding Japanese meant
 * scrolling past ninety entries, and the first screenful was Afrikaans,
 * Albanian, Amharic, Armenian. The shortlist has a stable curated order and
 * appends the user's interface language or saved choice when necessary.
 * Nothing is hidden: every language is still in the list below it, and typing
 * searches the translated name, the language's own name, the English name, and
 * the code.
 */
export function LanguageSelect({
  value,
  codes,
  onChange,
  autoLabel,
  ariaLabel,
  disabled = false,
  className,
  testId,
}: LanguageSelectProps) {
  const t = useT();
  const uiLanguage = useUiLanguage();

  const groups = useMemo<ComboboxGroup[]>(() => {
    const bands = groupedLanguageOptions(codes, uiLanguage, autoLabel, {
      preferred: uiLanguage,
      keepInCommon: value,
    });
    const headings: Record<string, string> = {
      common: t("language_select.group_common"),
      all: t("language_select.group_all"),
    };
    return bands.map((band) => ({
      id: band.id || "lead",
      label: headings[band.id],
      options: band.options.map<ComboboxOption>((option) => ({
        value: option.code,
        label: option.label,
        hint: option.endonym,
      })),
    }));
  }, [codes, uiLanguage, autoLabel, value, t]);

  // Matching lives in the language module, not in the generic combobox: the
  // English name and the code are searchable even though neither is on screen.
  const matches = useCallback(
    (option: ComboboxOption, query: string) =>
      option.value === "auto"
        ? languageMatches("auto", uiLanguage, query) ||
          option.label.toLocaleLowerCase().includes(query.toLocaleLowerCase())
        : languageMatches(option.value, uiLanguage, query),
    [uiLanguage],
  );

  return (
    <Combobox
      value={value}
      groups={groups}
      onChange={onChange}
      ariaLabel={ariaLabel}
      // A saved code the served list has not delivered yet (or no longer
      // carries) still reads as a language rather than as a bare "yue".
      fallbackLabel={languageName(value, uiLanguage, autoLabel)}
      searchPlaceholder={t("language_select.search")}
      emptyLabel={t("language_select.empty")}
      matches={matches}
      disabled={disabled}
      className={className}
      testId={testId}
    />
  );
}
