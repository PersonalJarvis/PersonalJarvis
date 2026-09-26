import { useState } from "react";
import { useT } from "@/i18n";
import { openExternalUrl } from "@/lib/openExternal";
import { openLocalPath } from "@/lib/openLocalPath";

export function TaskOutputLinks({ output, evidence }: { output: unknown; evidence: unknown }) {
  const t = useT();
  const [openFailed, setOpenFailed] = useState(false);
  const checked = new Set(Array.isArray(evidence) ? evidence.filter((item): item is string => typeof item === "string") : []);
  const links = Array.isArray(output) ? output.filter((item): item is string =>
    typeof item === "string" && (/^https?:\/\//i.test(item) || checked.has(item))) : [];
  if (!links.length) return null;
  const open = async (value: string, external: boolean) => {
    const opened = await (external ? openExternalUrl(value) : openLocalPath(value));
    setOpenFailed(!opened);
  };
  return <div>
    <div className="flex flex-wrap gap-2" aria-label={t("society.tasks.outputs")}>
      {links.map((value) => {
      const external = /^https?:\/\//i.test(value);
      const label = external ? value : value.replace(/\\/g, "/").split("/").pop() || value;
      return <button key={value} type="button"
        className="max-w-full truncate rounded-md border border-border px-2 py-1 text-left text-xs text-foreground hover:bg-secondary"
        title={value} onClick={() => void open(value, external)}>
        {label} · {t(checked.has(value) ? "society.tasks.verified_file" : "society.tasks.open_link")}
      </button>;
      })}
    </div>
    {openFailed && <p role="alert" className="mt-1 text-xs text-destructive">{t("society.tasks.open_failed")}</p>}
  </div>;
}
