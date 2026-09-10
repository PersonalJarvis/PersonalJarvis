import { describeToolStep } from "@/lib/toolStepLabel";
import { resolveToolBrand } from "@/lib/toolBrand";
import type { ToolBlock } from "./reduce";
import type { ToolChoice } from "./toolChoices";
import { toolIdentity } from "./toolIdentity";

/** Remove transport wrappers, retaining the actual server and operation. */
export function traceToolName(name: string): string {
  return name.replace(/^(?:functions\.|tools\.)/, "")
    .replace(/^mcp__codex_apps__/, "")
    .replace(/^mcp__([^_]+)__/, "$1/")
    .replace(/^mcp__/, "").replace(/__/g, "/");
}

export function traceAction(name: string): string | null {
  const key = traceToolName(name).toLowerCase().replace(/[-_]/g, "");
  if (/^(bash|powershell|shell|runshell|runshellcommand|execcommand|runcommand)$/.test(key)) return "command";
  if (/^(edit|editfile|applypatch|multiedit|strreplace)$/.test(key)) return "edit";
  if (/^(write|writefile|createfile)$/.test(key)) return "write";
  if (/^(read|readfile|viewfile|cat|openfile|readmediafile)$/.test(key)) return "read";
  if (/^(ls|listdir|listdirectory|listfiles|glob)$/.test(key)) return "list";
  if (/^(grep|rg|search|searchfiles|grepsearch|codesearch|findbyname)$/.test(key)) return "search";
  return null;
}

export function traceToolIdentity(block: ToolBlock) {
  const name = traceToolName(block.name);
  const description = describeToolStep(name, block.input);
  const brand = description.brand;
  const action = traceAction(name);
  const integration = Boolean(brand?.brandId || name.includes("/") || /^mcp__/.test(block.name));
  const service = brand?.brandId ? resolveToolBrand(brand.brandId).label
    : brand?.label ?? name.split("/")[0].replace(/[-_]/g, " ");
  const row: ToolChoice = {
    id: name, label: service, brand: brand?.brandId ?? "", group: "", skill: "",
    category: integration ? "plugins" : action === "command" ? "cli" : "system",
    description: "", available: true, tool_names: [block.name],
  };
  // The composer already knows bundled plugin/CLI marks and theme treatment.
  const identity = toolIdentity(row);
  return { description, action, row, identity, integration, service };
}

/** A factual receipt, derived only from completed calls, never their arguments. */
export function activityParts(blocks: ToolBlock[]) {
  const parts = new Map<string, { key: string; service?: string; row?: ToolChoice }>();
  for (const block of blocks) {
    const view = traceToolIdentity(block);
    if (view.integration) {
      const id = `service:${view.row.brand || view.service}`;
      parts.set(id, { key: "integration_used", service: view.service, row: view.row });
    } else {
      const key = view.action ? `activity_${view.action}` : "activity_tools";
      if (!parts.has(key)) parts.set(key, { key });
    }
  }
  return [...parts.values()];
}
