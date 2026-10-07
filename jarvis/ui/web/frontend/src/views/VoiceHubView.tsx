import { useEventStore } from "@/store/events";
import { PageHeader } from "@/components/layout/PageHeader";
import { SectionTabBar, type SectionTab } from "@/components/layout/SectionTabBar";
import { useT } from "@/i18n";
import { DictationView } from "@/views/DictationView";
import { DictionaryView } from "@/views/DictionaryView";
import { ShortcutsTab } from "@/views/voice/ShortcutsTab";
import { LanguageTab } from "@/views/voice/LanguageTab";
import { VoiceApiKeysTab } from "@/views/voice/VoiceApiKeysTab";

/**
 * Combined voice section — everything that turns speech into text, behind one
 * sidebar entry:
 *
 *   {name} Voice
 *   Dictation  Dictionary  Shortcuts  Language  API Keys
 *   ─────────
 *   <the active tab's page>
 *
 * The active sidebar section id IS the tab state, so voice deep-links ("open
 * the dictionary") and the NavigateSidebar event keep landing on the right
 * tab with no extra routing.
 *
 * The title, the tab bar and every tab's page share ONE centred column (the
 * `max-w-3xl` measure of `VoicePage` in `voice/voiceUi.tsx`), so the heading,
 * the tabs and the content start on the same left edge at any window width.
 * The title is drawn once, here; every child is asked to stand its own header
 * down via `hideHeader`.
 *
 * The title comes from the `nav.voice` locale value, which carries the
 * `{name}` token; `useT()` substitutes the live wake-word brand. Never
 * hardcode a name here.
 */

const TABS = [
  { id: "dictation", labelKey: "nav.dictation" },
  { id: "dictionary", labelKey: "nav.dictionary" },
  { id: "voice-shortcuts", labelKey: "nav.voice_shortcuts" },
  { id: "voice-language", labelKey: "nav.voice_language" },
  { id: "voice-api-keys", labelKey: "nav.voice_api_keys" },
] as const satisfies readonly SectionTab[];

export function VoiceHubView() {
  const t = useT();
  const active = useEventStore((s) => s.activeSection);

  // The router only mounts us for the five ids above; any other value is
  // unexpected — fall back to the Dictation tab defensively.
  const current = TABS.some((tab) => tab.id === active) ? active : "dictation";

  return (
    <div className="flex h-full flex-col" data-testid="voice-hub">
      <div className="shrink-0">
        <div className="mx-auto w-full max-w-3xl px-4 sm:px-8">
          <PageHeader
            title={t("nav.voice")}
            description={t("voice.hub.subtitle")}
            tabs={
              // Five labels outgrow a phone-width column; the bar scrolls
              // sideways there instead of wrapping onto a second line.
              <nav aria-label={t("nav.voice")} className="overflow-x-auto">
                <SectionTabBar tabs={TABS} className="min-w-max" />
              </nav>
            }
          />
        </div>
      </div>
      {/* min-h-0 is load-bearing: without it a flex child with its own
          scroll container grows past the viewport instead of scrolling. */}
      <div className="min-h-0 flex-1 overflow-hidden">
        {current === "dictation" && <DictationView hideHeader />}
        {current === "dictionary" && <DictionaryView hideHeader />}
        {current === "voice-shortcuts" && <ShortcutsTab hideHeader />}
        {current === "voice-language" && <LanguageTab hideHeader />}
        {current === "voice-api-keys" && <VoiceApiKeysTab hideHeader />}
      </div>
    </div>
  );
}
