// Component tests start at the same ready-resource boundary as main.tsx.
// Import only the data loader: do not pre-cache React hooks or stores that tests mock.
import { CORE_LANGUAGES, loadCoreLocale } from "@/i18n/coreLocales";
await Promise.all(CORE_LANGUAGES.map(loadCoreLocale));
