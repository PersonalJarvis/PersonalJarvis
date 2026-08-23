import { ChevronDown, ExternalLink, GitFork } from "lucide-react";
import type { AltCredential } from "@/hooks/useProviders";
import { useT } from "@/i18n";

/**
 * Renders a provider's ALTERNATIVE credential path. Gemini's AI-Studio-vs-Vertex
 * split is the only one today: the primary key form sits above, and this note
 * makes the Vertex route — a separate Google Cloud billing project — explicit
 * so a user does not top up one account while Jarvis bills the other.
 *
 * A disclosure, closed by default: the alternative is named on one line and
 * its (long) instructions open on demand. Open, it was the tallest thing on
 * the card — eight lines of Cloud-project setup shown to everyone, including
 * the many who pasted an AI Studio key and were done.
 */
export function AltCredentialNote({ alt }: { alt: AltCredential }) {
  const t = useT();
  return (
    <details className="group text-xs">
      <summary className="inline-flex cursor-pointer list-none items-center gap-1.5 text-muted-foreground transition-colors hover:text-foreground [&::-webkit-details-marker]:hidden">
        <GitFork aria-hidden="true" className="h-3 w-3" />
        <span className="font-medium text-foreground/90">
          {t("apikeys_view.alt_credential_title").replace("{0}", alt.label)}
        </span>
        <span className="text-muted-foreground">· {t(`provider_billing.${alt.billing}`)}</span>
        <ChevronDown
          aria-hidden="true"
          className="h-3 w-3 transition-transform group-open:rotate-180 motion-reduce:transition-none"
        />
      </summary>
      <div className="mt-1.5 space-y-1 pl-[18px]">
        <p className="max-w-prose leading-relaxed text-muted-foreground">{alt.credential_help}</p>
        {alt.credential_path_hint && (
          <p className="font-mono text-muted-foreground/80">{alt.credential_path_hint}</p>
        )}
        {alt.dashboard_url && (
          <a
            href={alt.dashboard_url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-muted-foreground hover:text-primary"
          >
            <ExternalLink aria-hidden="true" className="h-3 w-3" />{" "}
            {t("apikeys_view.alt_credential_setup").replace("{0}", alt.label)}
          </a>
        )}
      </div>
    </details>
  );
}
