import { useEffect, useRef, useState } from "react";
import { Check, Copy } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * Shiki-basierter Code-Block mit Copy-Button und Sprach-Tag.
 *
 * Shiki ist ESM-only und groesse (~150 KB). Wir laden den Highlighter lazy
 * pro CodeBlock-Mount; Shiki cached die Sprache intern, also ist der zweite
 * Mount schnell. Bei Render-Performance-Problem koennen wir auf einen
 * geteilten Singleton umstellen — Tier-1 ist pragmatisch.
 *
 * Falls die Sprache nicht erkannt wird, fallback auf ``txt`` ohne Highlight.
 */
type ShikiHighlighterApi = {
  codeToHtml: (code: string, opts: { lang: string; theme: string }) => string;
};

let highlighterPromise: Promise<ShikiHighlighterApi> | null = null;

const SUPPORTED_LANGS = [
  "bash", "shell", "sh", "powershell", "ps1",
  "python", "py", "typescript", "ts", "tsx", "javascript", "js", "jsx",
  "json", "yaml", "yml", "toml", "ini", "xml", "html", "css",
  "rust", "go", "java", "c", "cpp", "csharp", "kotlin", "swift",
  "sql", "diff", "markdown", "md",
];

async function loadHighlighter(): Promise<ShikiHighlighterApi> {
  if (highlighterPromise) return highlighterPromise;
  highlighterPromise = import("shiki").then(async (shiki) => {
    return await shiki.createHighlighter({
      themes: ["github-dark-default"],
      langs: SUPPORTED_LANGS,
    });
  });
  return highlighterPromise;
}

interface CodeBlockProps {
  language: string;
  code: string;
}

export function CodeBlock({ language, code }: CodeBlockProps) {
  const [html, setHtml] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const lang = SUPPORTED_LANGS.includes(language) ? language : "txt";
    if (lang === "txt") {
      setHtml(null);
      return;
    }
    void loadHighlighter().then((highlighter) => {
      if (cancelled || !mountedRef.current) return;
      try {
        const out = highlighter.codeToHtml(code, {
          lang,
          theme: "github-dark-default",
        });
        setHtml(out);
      } catch {
        setHtml(null);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [language, code]);

  const handleCopy = () => {
    void navigator.clipboard.writeText(code).then(() => {
      setCopied(true);
      window.setTimeout(() => {
        if (mountedRef.current) setCopied(false);
      }, 1500);
    });
  };

  return (
    <div className="not-prose group relative my-4 overflow-hidden rounded-md border border-border bg-[#0a0c10]">
      {/* Header-Bar */}
      <div className="flex items-center justify-between border-b border-border/40 bg-muted/20 px-3 py-1">
        <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
          {language || "text"}
        </span>
        <button
          type="button"
          onClick={handleCopy}
          className={cn(
            "rounded p-1 text-muted-foreground opacity-60 transition",
            "hover:bg-muted hover:text-foreground hover:opacity-100",
            "group-hover:opacity-100",
          )}
          title="Code kopieren"
        >
          {copied ? (
            <Check className="h-3 w-3 text-emerald-400" />
          ) : (
            <Copy className="h-3 w-3" />
          )}
        </button>
      </div>
      {/* Body — entweder Shiki-HTML oder Plain-Text */}
      {html ? (
        <div
          className="overflow-x-auto px-3 py-2 text-xs leading-relaxed [&_pre]:m-0 [&_pre]:!bg-transparent"
          // sanitized bei Shiki — unser Code wird nur lokal gerendert
          dangerouslySetInnerHTML={{ __html: html }}
        />
      ) : (
        <pre className="m-0 overflow-x-auto px-3 py-2 text-xs leading-relaxed text-foreground/90">
          <code>{code}</code>
        </pre>
      )}
    </div>
  );
}
