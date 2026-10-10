import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import type { OutputBundle } from "rollup";
import type { Plugin } from "vite";

interface SourceDocument {
  file: string;
  source: string;
  sha256: string;
}

interface PackageSource {
  version: string;
  reason?: string;
  documents: SourceDocument[];
}

interface PackageNotice {
  name: string;
  version: string;
  documents: Map<string, string>;
  hasLicense: boolean;
}

const NOTICE_NAME = "THIRD_PARTY_NOTICES.txt";
const LEGAL_FILE = /^(?:licen[cs]e|copying|copyright(?:notice)?|notice|third[-_ ]?party[-_ ]?(?:notices?|licenses?))(?:$|[._-])/i;
const LICENSE_FILE = /^(?:licen[cs]e|copying)(?:$|[._-])/i;
const TEXT_FILE = /(?:\.(?:txt|md|markdown|rst)$|^[^.]+$)/i;

function legalDocuments(directory: string): string[] {
  if (!fs.existsSync(directory)) return [];
  return fs.readdirSync(directory, { withFileTypes: true })
    .filter((entry) => entry.isFile() && LEGAL_FILE.test(entry.name)
      && TEXT_FILE.test(entry.name) && !/\.(?:js|json|svg|map)$/i.test(entry.name))
    .map((entry) => path.join(directory, entry.name))
    .sort();
}

/** Only files that actually reach chunks/assets contribute package notices. */
export class NoticeCollector {
  private readonly packages = new Map<string, PackageNotice>();
  private readonly sources: Record<string, PackageSource>;
  private readonly repository: string;
  private readonly errors = new Set<string>();

  constructor(private readonly root: string) {
    this.repository = path.resolve(root, "../../../..");
    const manifest = path.join(this.repository, "third_party/frontend-notice-sources.json");
    this.sources = fs.existsSync(manifest)
      ? JSON.parse(fs.readFileSync(manifest, "utf8")).packages : {};
  }

  addFile(id: string): void {
    const file = path.resolve(this.root, id.replace(/^\0/, "").split("?", 1)[0]);
    const normalized = file.replace(/\\/g, "/");
    const marker = normalized.lastIndexOf("/node_modules/");
    if (marker < 0) return;
    const segments = normalized.slice(marker + 14).split("/");
    const length = segments[0].startsWith("@") ? 2 : 1;
    const packageRoot = path.resolve(normalized.slice(0, marker + 14), ...segments.slice(0, length));
    const metadataFile = path.join(packageRoot, "package.json");
    if (!fs.existsSync(metadataFile)) {
      throw new Error(`Cannot identify bundled package: ${segments.slice(0, length).join("/")}`);
    }
    const metadata = JSON.parse(fs.readFileSync(metadataFile, "utf8")) as { name: string; version: string };
    let notice = this.packages.get(packageRoot);
    if (!notice) {
      notice = { name: metadata.name, version: metadata.version, documents: new Map(), hasLicense: false };
      this.packages.set(packageRoot, notice);
      const rootDocuments = legalDocuments(packageRoot);
      for (const document of rootDocuments) this.addDocument(notice, document, path.basename(document));
      // Some npm tarballs omit LICENSE. Each exception is version-pinned and
      // retains a real, unedited upstream document with its source and digest.
      const source = this.sources[notice.name];
      if (source) {
        if (source.version !== notice.version) {
          throw new Error(`Review notice source for ${notice.name}@${notice.version}; recorded version is ${source.version}`);
        }
        if (source.reason) notice.documents.set("Source provenance", source.reason);
        for (const document of source.documents) {
          const text = fs.readFileSync(path.join(this.repository, document.file), "utf8").replace(/\r\n?/g, "\n");
          if (createHash("sha256").update(text).digest("hex") !== document.sha256) {
            throw new Error(`Notice source digest changed: ${document.file}`);
          }
          this.addText(notice, document.file, text, `${document.file}\nSource: ${document.source}`);
        }
      }
    }
    if (!notice.hasLicense) {
      throw new Error(`No full license document for bundled ${notice.name}@${notice.version}; add a verified, version-pinned upstream source`);
    }
    // Vendored submodules (e.g. victory-vendor/lib-vendor/d3-*) have their own
    // licenses. Collect only the ancestor directories of the emitted files.
    let directory = path.dirname(file);
    while (directory !== packageRoot && directory.startsWith(packageRoot + path.sep)) {
      for (const document of legalDocuments(directory)) {
        this.addDocument(notice, document, path.relative(packageRoot, document).replace(/\\/g, "/"));
      }
      directory = path.dirname(directory);
    }
  }

  private addDocument(notice: PackageNotice, file: string, label: string): void {
    const text = fs.readFileSync(file, "utf8").replace(/\r\n?/g, "\n").trim();
    this.addText(notice, file, text, label);
  }

  private addText(notice: PackageNotice, file: string, text: string, label: string): void {
    const terms = text.trim();
    if (LICENSE_FILE.test(path.basename(file))) {
      if (terms.length < 100) throw new Error(`Incomplete legal document for ${notice.name}: ${label}`);
      notice.hasLicense = true;
    }
    // Copyright notices can legitimately be a single line. They must not be
    // used as substitutes for a full license, nor rejected for being short.
    if (terms) notice.documents.set(label, terms);
  }

  collect(bundle: OutputBundle): void {
    const collectFile = (file: string) => {
      try { this.addFile(file); }
      catch (error) { this.errors.add(error instanceof Error ? error.message : String(error)); }
    };
    for (const output of Object.values(bundle)) {
      if (output.type === "chunk") {
        for (const [id, module] of Object.entries(output.modules)) {
          // Extracted CSS still ships even when its JS representation is empty.
          if (module.renderedLength > 0 || /\.css(?:\?|$)/i.test(id)) collectFile(id);
        }
      } else {
        for (const file of output.originalFileNames) collectFile(file);
      }
    }
  }

  render(): string {
    if (this.errors.size) {
      throw new Error(`Third-party notice generation failed:\n${Array.from(this.errors).sort().join("\n")}`);
    }
    const copied = fs.readFileSync(path.join(this.root, "public", NOTICE_NAME), "utf8").replace(/\r\n?/g, "\n").trim();
    const entries = Array.from(this.packages.entries())
      .sort(([a, left], [b, right]) => {
        const first = `${left.name}@${left.version}`;
        const second = `${right.name}@${right.version}`;
        return first < second ? -1 : first > second ? 1 : a < b ? -1 : a > b ? 1 : 0;
      })
      .map(([, notice]) => {
        const documents = Array.from(notice.documents.entries()).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)
          .map(([label, text]) => `${label}\n\n${text}`).join("\n\n");
        return `${notice.name}@${notice.version}\n\n${documents}`;
      });
    return [copied, "Bundled production packages (including web workers and font assets)", ...entries]
      .join("\n\n----------------------------------------------------------------------\n\n") + "\n";
  }
}

/** Share the collector with Vite's separate worker builds before main output. */
export function thirdPartyNotices(root: string): { plugin: Plugin; workerPlugin: () => Plugin } {
  let collector: NoticeCollector;
  return {
    plugin: {
      name: "third-party-notices",
      apply: "build",
      buildStart() { collector = new NoticeCollector(root); },
      generateBundle: {
        order: "post",
        handler(_options, bundle) {
          collector.collect(bundle);
          // These SVGs are copied directly by materialIconAssets, outside Rollup.
          collector.addFile(path.join(root, "node_modules/material-icon-theme/icons"));
          this.emitFile({ type: "asset", fileName: NOTICE_NAME, source: collector.render() });
        },
      },
    },
    workerPlugin: () => ({
      name: "worker-third-party-notices",
      generateBundle(_options, bundle) { collector.collect(bundle); },
    }),
  };
}
