import { Children, isValidElement, type ReactNode } from "react";

/** Read block code without confusing it with inline code. */
export function readFence(children: ReactNode): { language: string; code: string } | null {
  const element = Children.toArray(children).find(child => isValidElement(child));
  if (!isValidElement<{ className?: string; children?: ReactNode }>(element)) return null;
  return {
    language: /language-(\S+)/.exec(element.props.className ?? "")?.[1].toLowerCase() ?? "",
    code: String(element.props.children ?? "").replace(/\n$/, ""),
  };
}

/** Infer only complete visual roots; ordinary code and explicit text stay source. */
export function visualFenceLanguage(language: string, code: string): "html" | "svg" | null {
  const tag = language.toLowerCase();
  if (["html", "htm", "text/html"].includes(tag)) return "html";
  if (["svg", "image/svg+xml"].includes(tag)) return "svg";
  if (tag !== "" && tag !== "xml") return null;
  const source = code.trim().replace(/^<\?xml\s[^?]*\?>\s*/i, "");
  if (/^<svg(?:\s[^>]*)?>[\s\S]*<\/svg>$/i.test(source)) return "svg";
  if (tag === "" && /^(?:<!doctype html>\s*)?<html(?:\s[^>]*)?>[\s\S]*<\/html>$/i.test(source)) return "html";
  return null;
}
