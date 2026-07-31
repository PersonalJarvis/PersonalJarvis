import { Mic } from "lucide-react";

import { useEventStore } from "@/store/events";
import { SectionTabBar, type SectionTab } from "@/components/layout/SectionTabBar";
import { ViewHeader } from "@/views/ChatsView";
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
 *   [ Dictation ] [ Dictionary ] [ Shortcuts ] [ Language ] [ API Keys ]
 *
 * Same merged-section pattern as ExtensionsView / ClisHubView: the active
 * sidebar section id IS the tab state, so voice deep-links ("open the
 * dictionary") and the NavigateSidebar event keep landing on the right tab with
 * no extra routing.
 *
 * One difference from those two precedents, and it is deliberate: the section
 * title is rendered ONCE here, above the tab bar, instead of each child drawing
 * its own header below it. Two stacked bordered bands read as a rendering fault,
 * and the merged brand ("{name} Voice") belongs above the tabs it fronts — so
 * every child is asked to stand its header down via `hideHeader`.
 *
 * The title comes from the `nav.voice` locale value, which carries the `{name}`
 * token; `useT()` substitutes the live wake-word brand. Never hardcode a name
 * here.
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
    <div className="flex h-full flex-col">
      <ViewHeader
        icon={<Mic className="h-4 w-4 text-primary" />}
        title={t("nav.voice")}
        subtitle={t("voice.hub.subtitle")}
      />
      <SectionTabBar tabs={TABS} />
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
