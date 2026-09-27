import { ChatMarkdown } from "@/components/agentchat/ChatMarkdown";
import { memoryDiff, type DiffLine } from "@/components/agentchat/toolDiff";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

/** Hide storage bookkeeping without modifying the canonical Markdown file. */
export function readableMemory(text: string): string {
  return text.replace(/^\uFEFF/, "")
    .replace(/^---\r?\n[\s\S]*?\r?\n---(?:\r?\n|$)/, "")
    .replace(/^<!-- memory-entry: [^\r\n]* -->\r?\n?/gm, "")
    .replace(/\n{3,}/g, "\n\n").trim();
}

export function MemoryMarkdown({ text }: { text: string }) {
  const t = useT();
  const content = readableMemory(text);
  return content ? <ChatMarkdown text={content} className="text-sm leading-7 [overflow-wrap:anywhere]" />
    : <p className="text-sm text-muted-foreground">{t("society.profile_card.empty_file")}</p>;
}

export function MemoryChanges({ before, after, patch }: { before: string; after: string; patch?: string[] }) {
  const t = useT();
  const files = memoryDiff("society_wiki_note", {}, { before: readableMemory(before), after: readableMemory(after) }) ?? [];
  const groups: { kind: string; lines: string[] }[] = [];
  const lines: DiffLine[] = patch ? patch.map(line => ({ kind: line.startsWith("@@") ? "gap" : line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : "ctx", text: line.startsWith("@@") ? "…" : line.slice(1) })) : files.flatMap(file => file.lines);
  for (const line of lines) {
    const previous = groups.at(-1);
    if (previous?.kind === line.kind) previous.lines.push(line.text);
    else groups.push({ kind: line.kind, lines: [line.text] });
  }
  return <div aria-label={t("work_trace.diff")} className="space-y-1">
    {groups.map((group, index) => <div key={index} data-change={group.kind} className={cn("flex gap-3 rounded-md px-3 py-2", group.kind === "add" && "diff-line-add", group.kind === "del" && "diff-line-del", group.kind === "ctx" && "text-muted-foreground")}>
      <span aria-hidden className="w-3 shrink-0 font-mono">{group.kind === "add" ? "+" : group.kind === "del" ? "−" : ""}</span>
      <div className="min-w-0 flex-1"><ChatMarkdown text={group.lines.join("\n")} className="text-sm leading-7 [overflow-wrap:anywhere]" /></div>
    </div>)}
  </div>;
}
