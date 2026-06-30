import { MessageSquareWarning } from "lucide-react";

import { ViewHeader } from "@/views/ChatsView";
import { useT } from "@/i18n";
import { openExternalUrl } from "@/lib/openExternal";

/**
 * The Feedback section — bugs, ideas, and questions are reported exclusively in
 * the project's Discord #bug-reports channel. The button forwards the user
 * straight there via a public, never-expiring invite link.
 *
 * The button uses {@link openExternalUrl} rather than a bare
 * `<a target="_blank">`, because the desktop shell (WebView2) silently drops
 * `target="_blank"` / `window.open` — the new tab never appears. The bridge asks
 * the backend to open the OS default browser instead (and falls back to
 * `window.open` on a remote/VPS browser).
 */

// Public, never-expiring Discord invite that drops the visitor directly into the
// #bug-reports channel of the PersonalJarvis server. An invite link is meant to
// be shared, so it is safe to ship in the client.
const DISCORD_INVITE_URL = "https://discord.gg/9QesfUrtq";

/** The official Discord brand mark (inline so it renders without an asset). */
function DiscordIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className={className} aria-hidden="true">
      <path d="M20.317 4.369a19.79 19.79 0 0 0-4.885-1.515.074.074 0 0 0-.079.037c-.21.375-.444.864-.608 1.249a18.27 18.27 0 0 0-5.487 0 12.6 12.6 0 0 0-.617-1.25.077.077 0 0 0-.079-.036A19.736 19.736 0 0 0 3.677 4.37a.07.07 0 0 0-.032.027C.533 9.046-.32 13.58.099 18.057a.082.082 0 0 0 .031.057 19.9 19.9 0 0 0 5.993 3.03.078.078 0 0 0 .084-.028c.462-.63.874-1.295 1.226-1.994a.076.076 0 0 0-.041-.106 13.1 13.1 0 0 1-1.872-.892.077.077 0 0 1-.008-.128c.126-.094.252-.192.372-.291a.074.074 0 0 1 .077-.01c3.928 1.793 8.18 1.793 12.062 0a.074.074 0 0 1 .078.009c.12.099.246.198.373.292a.077.077 0 0 1-.006.127 12.3 12.3 0 0 1-1.873.892.077.077 0 0 0-.041.107c.36.698.772 1.362 1.225 1.993a.076.076 0 0 0 .084.028 19.84 19.84 0 0 0 6.002-3.03.077.077 0 0 0 .032-.054c.5-5.177-.838-9.674-3.549-13.66a.061.061 0 0 0-.031-.03zM8.02 15.331c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.956-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.956 2.418-2.157 2.418zm7.975 0c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.955-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.946 2.418-2.157 2.418z" />
    </svg>
  );
}

export function FeedbackView() {
  const t = useT();

  return (
    <div className="flex h-full flex-col overflow-y-auto scrollbar-jarvis">
      <ViewHeader
        icon={<MessageSquareWarning className="h-4 w-4 text-primary" />}
        title={t("nav.feedback")}
        subtitle={t("feedback.subtitle")}
      />

      <div className="flex flex-1 items-center justify-center p-6">
        <div className="w-full max-w-md rounded-2xl border border-[#5865F2]/40 bg-[#5865F2]/10 p-8 text-center">
          <div className="mx-auto mb-5 flex h-16 w-16 items-center justify-center rounded-2xl bg-[#5865F2]/20">
            <DiscordIcon className="h-9 w-9 text-[#5865F2]" />
          </div>
          <h2 className="mb-2 text-lg font-semibold text-foreground">
            {t("feedback.discord_cta_title")}
          </h2>
          <p className="mb-6 text-sm text-muted-foreground">
            {t("feedback.discord_cta_subtitle")}
          </p>
          <button
            type="button"
            onClick={() => void openExternalUrl(DISCORD_INVITE_URL)}
            className="w-full rounded-lg bg-[#5865F2] px-4 py-3 text-sm font-semibold text-white transition hover:bg-[#4752c4]"
          >
            {t("feedback.discord_cta_button")}
          </button>
        </div>
      </div>
    </div>
  );
}
