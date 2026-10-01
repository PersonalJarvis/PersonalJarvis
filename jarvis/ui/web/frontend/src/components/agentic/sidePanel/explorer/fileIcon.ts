import {
  File,
  FileArchive,
  FileCode,
  FileCog,
  FileImage,
  FileJson,
  FileLock,
  FileSpreadsheet,
  FileTerminal,
  FileText,
  FileType,
  type LucideIcon,
} from "lucide-react";

const BY_EXTENSION: Record<string, LucideIcon> = {};
const register = (icon: LucideIcon, extensions: string[]) => {
  for (const extension of extensions) BY_EXTENSION[extension] = icon;
};
register(FileCode, [
  "ts", "tsx", "js", "jsx", "mjs", "cjs", "py", "pyi", "rs", "go", "java", "kt", "swift", "c", "cc",
  "cpp", "h", "hpp", "cs", "rb", "php", "vue", "svelte", "html", "htm", "css", "scss", "sass", "less",
  "sql", "astro",
]);
register(FileJson, ["json", "jsonl", "jsonc"]);
register(FileCog, ["toml", "yaml", "yml", "ini", "cfg", "conf", "env", "editorconfig"]);
register(FileText, ["md", "mdx", "txt", "rst", "log"]);
register(FileImage, ["png", "jpg", "jpeg", "gif", "svg", "webp", "ico", "bmp", "avif"]);
register(FileTerminal, ["sh", "bash", "zsh", "fish", "ps1", "bat", "cmd"]);
register(FileArchive, ["zip", "tar", "gz", "tgz", "7z", "rar", "whl"]);
register(FileSpreadsheet, ["csv", "tsv", "xlsx", "xls"]);
register(FileLock, ["lock"]);
register(FileType, ["ttf", "otf", "woff", "woff2"]);

/** A generic, brand-free glyph for a file, chosen by its extension. */
export function fileIcon(name: string): LucideIcon {
  const lower = name.toLowerCase();
  if (lower.startsWith(".env")) return FileCog;
  const dot = lower.lastIndexOf(".");
  if (dot <= 0) return File;
  return BY_EXTENSION[lower.slice(dot + 1)] ?? File;
}
