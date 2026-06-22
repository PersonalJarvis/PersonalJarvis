import { ExternalLink, GitFork } from "lucide-react";
import type { AltCredential } from "@/hooks/useProviders";
import { ProviderBillingBadge } from "@/components/ProviderBillingBadge";

/**
 * Renders a provider's ALTERNATIVE credential path. Gemini's AI-Studio-vs-Vertex
 * split is the only one today: the primary key form sits above, and this note
 * makes the Vertex route — a separate Google Cloud billing project — explicit
 * so a user does not top up one account while Jarvis bills the other.
 */
export function AltCredentialNote({ alt }: { alt: AltCredential }) {
  return (
    <div className="rounded-md border border-dashed border-border bg-muted/20 p-2.5">
      <div className="mb-1 flex items-center gap-1.5">
        <GitFork className="h-3 w-3 text-muted-foreground" />
        <span className="text-[11px] font-medium text-foreground">
          Alternative: {alt.label}
        </span>
        <ProviderBillingBadge billing={alt.billing} />
      </div>
      <p className="text-[11px] leading-relaxed text-muted-foreground">{alt.credential_help}</p>
      {alt.credential_path_hint && (
        <p className="mt-1 font-mono text-[10px] text-muted-foreground/80">
          {alt.credential_path_hint}
        </p>
      )}
      {alt.dashboard_url && (
        <a
          href={alt.dashboard_url}
          target="_blank"
          rel="noreferrer"
          className="mt-1 inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-primary"
        >
          <ExternalLink className="h-3 w-3" /> Set up {alt.label}
        </a>
      )}
    </div>
  );
}
