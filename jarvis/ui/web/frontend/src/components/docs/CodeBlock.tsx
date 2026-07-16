import { useEffect, useRef, useState } from "react";
import { Check, Copy } from "lucide-react";

import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

/**
 * Shiki-based code block with copy button and language tag.
 *
 * Shiki is ESM-only and large, so each language is lazy-loaded only when a
 * guide actually contains that kind of code. Highlighters are cached per
 * canonical language; opening ordinary prose docs never downloads Shiki.
 *
 * If the language isn't recognized, fall back to ``txt`` without highlighting.
 */
type ShikiHighlighterApi = {
  codeToHtml: (code: string, opts: { lang: string; theme: string }) => string;
};

const highlighterPromises = new Map<string, Promise<ShikiHighlighterApi>>();

const SUPPORTED_LANGS = [
  "bash", "shell", "sh", "powershell", "ps1",
  "python", "py", "typescript", "ts", "tsx", "javascript", "js", "jsx",
  "json", "yaml", "yml", "toml", "ini", "xml", "html", "css",
  "rust", "go", "java", "c", "cpp", "csharp", "kotlin", "swift",
  "sql", "diff", "markdown", "md",
];

const LANGUAGE_ALIASES: Record<string, string> = {
  shell: "bash",
  sh: "bash",
  ps1: "powershell",
  py: "python",
  ts: "typescript",
  js: "javascript",
  yml: "yaml",
  md: "markdown",
};

function canonicalLanguage(language: string): string {
  return LANGUAGE_ALIASES[language] ?? language;
}

async function loadHighlighter(language: string): Promise<ShikiHighlighterApi> {
  const cached = highlighterPromises.get(language);
  if (cached) return cached;
  const promise = import("shiki").then(async (shiki) => {
    return await shiki.createHighlighter({
      themes: ["github-dark-default"],
      langs: [language],
    });
  });
  highlighterPromises.set(language, promise);
  return promise;
}

interface CodeBlockProps {
  language: string;
  code: string;
}

export function CodeBlock({ language, code }: CodeBlockProps) {
  const t = useT();
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
    const lang = SUPPORTED_LANGS.includes(language)
      ? canonicalLanguage(language)
      : "txt";
    if (lang === "txt") {
      setHtml(null);
      return;
    }
    void loadHighlighter(lang)
      .then((highlighter) => {
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
      })
      .catch(() => {
        highlighterPromises.delete(lang);
        if (!cancelled && mountedRef.current) setHtml(null);
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
          title={t("docs_content.copy_code")}
          aria-label={
            copied ? t("docs_content.code_copied") : t("docs_content.copy_code")
          }
        >
          {copied ? (
            <Check className="h-3 w-3 text-emerald-400" aria-hidden="true" />
          ) : (
            <Copy className="h-3 w-3" aria-hidden="true" />
          )}
          <span className="sr-only" aria-live="polite">
            {copied ? t("docs_content.code_copied") : ""}
          </span>
        </button>
      </div>
      {/* Body — either Shiki HTML or plain text */}
      {html ? (
        <div
          className="overflow-x-auto px-3 py-2 text-xs leading-relaxed [&_pre]:m-0 [&_pre]:!bg-transparent"
          // sanitized by Shiki — our code is only rendered locally
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
