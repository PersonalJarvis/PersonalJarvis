import { useUiLanguage } from "@/i18n";
import en from "@/i18n/locales/swarm/en.json";
import de from "@/i18n/locales/swarm/de.json";
import es from "@/i18n/locales/swarm/es.json";
const messages = { en, de, es };
export type SwarmText = (key: string) => string;
export function useSwarmText(): SwarmText {
  const language = useUiLanguage();
  const dictionary: Record<string, string> = messages[language];
  return key => dictionary[key] ?? key;
}
