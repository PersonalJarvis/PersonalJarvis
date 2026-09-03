/**
 * The categories the assistant refuses to hold.
 *
 * A file about a person is only trustworthy if it says what it will not
 * contain, and this one does — `## Do Not Record` has been in USER.md from
 * the beginning, enforced by `_DO_NOT_RECORD` in
 * `jarvis/plugins/tool/profile_update.py`, and shown to the reader nowhere.
 *
 * Quoted from the file rather than restated in code, so the page can never
 * promise something the file no longer says. Neutral, not `fault`: a
 * boundary the product keeps on purpose is not an error state.
 */
import { ShieldCheck } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useT } from "@/i18n";
import { parseDoNotRecord, shortenCategory } from "@/views/profile/provenance";

export function NeverStoredCard({ raw }: { raw: string | null | undefined }) {
  const t = useT();
  const categories = parseDoNotRecord(raw);

  if (categories.length === 0) return null;

  return (
    <Card className="profile-rise">
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2">
          <ShieldCheck aria-hidden className="h-4 w-4 text-muted-foreground" />
          {t("profile_view.never_title")}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-meta text-muted-foreground">{t("profile_view.never_body")}</p>
        <ul data-testid="never-stored" className="mt-3 flex flex-col gap-1.5">
          {categories.map((c) => (
            <li
              key={c}
              title={c}
              className="flex items-baseline gap-2 text-body text-foreground [overflow-wrap:anywhere]"
            >
              <span aria-hidden className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-muted-foreground" />
              {shortenCategory(c)}
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
