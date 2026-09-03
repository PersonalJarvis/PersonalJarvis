/**
 * Your standing instructions to the assistant.
 *
 * These live in their own section and are edited there. They appear here
 * because they belong to the same question the page answers: the profile is
 * what the assistant worked out about you, and this is what you told it
 * outright. Showing the first lines beside the inferred facts is the only
 * place in the product where both halves of the record are visible at once.
 *
 * Read-only on purpose — one editor per file, and this file's editor is the
 * section this card links to.
 */
import { ArrowRight, ScrollText } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useAgentInstructions } from "@/hooks/useAgentInstructions";
import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";

/** The opening of the file: enough to recognise, never the whole document. */
function excerpt(content: string, maxLines = 4, maxChars = 220): string {
  const lines = content
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith("#"));
  const head = lines.slice(0, maxLines).join(" ");
  return head.length > maxChars ? `${head.slice(0, maxChars).trimEnd()}…` : head;
}

export function RulesCard() {
  const t = useT();
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const { config, loading } = useAgentInstructions();

  const content = config?.content?.trim() ?? "";
  const has = !!config?.exists && content.length > 0;
  const lineCount = has ? content.split("\n").filter((l) => l.trim()).length : 0;

  return (
    <Card className="profile-rise">
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2">
          <ScrollText aria-hidden className="h-4 w-4 text-muted-foreground" />
          {t("profile_view.rules_title")}
        </CardTitle>
      </CardHeader>

      <CardContent>
        {loading ? (
          <div role="status" aria-busy="true" className="flex flex-col gap-2">
            <div className="h-3 w-full animate-pulse rounded-full bg-sheen/[0.06]" />
            <div className="h-3 w-3/4 animate-pulse rounded-full bg-sheen/[0.06]" />
          </div>
        ) : has ? (
          <>
            <blockquote
              data-testid="rules-excerpt"
              className="border-l-2 border-border-strong pl-3 text-meta italic text-foreground [overflow-wrap:anywhere]"
            >
              {excerpt(content)}
            </blockquote>
            <Button
              type="button"
              variant="link"
              className="mt-3 px-0"
              onClick={() => setActiveSection("agent-instructions")}
            >
              {t("profile_view.rules_open")
                .replace("{0}", String(lineCount))
                .replace("{1}", config?.filename ?? "")}
              <ArrowRight />
            </Button>
          </>
        ) : (
          <div data-testid="rules-empty">
            <p className="text-body text-muted-foreground">{t("profile_view.rules_empty_body")}</p>
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="mt-3"
              onClick={() => setActiveSection("agent-instructions")}
            >
              {t("profile_view.rules_write")}
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
