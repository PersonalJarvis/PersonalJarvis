import { useEffect, useState } from "react";
import materialIconManifestUrl from "material-icon-theme/dist/material-icons.json?url&no-inline";

import type { Theme } from "@/hooks/useTheme";

interface IconDefinition {
  iconPath: string;
}

interface ThemeOverrides {
  fileExtensions?: Record<string, string>;
  fileNames?: Record<string, string>;
  folderNames?: Record<string, string>;
  folderNamesExpanded?: Record<string, string>;
  rootFolderNames?: Record<string, string>;
  rootFolderNamesExpanded?: Record<string, string>;
}

export interface MaterialIconManifest {
  iconDefinitions: Record<string, IconDefinition>;
  fileExtensions: Record<string, string>;
  fileNames: Record<string, string>;
  folderNames: Record<string, string>;
  folderNamesExpanded: Record<string, string>;
  rootFolderNames: Record<string, string>;
  rootFolderNamesExpanded: Record<string, string>;
  light?: ThemeOverrides;
  file: string;
  folder: string;
  folderExpanded: string;
  rootFolder: string;
  rootFolderExpanded: string;
}

export interface MaterialFileIcon {
  id: string;
  src: string;
}

interface MaterialFileIconOptions {
  name: string;
  path?: string;
  directory?: boolean;
  open?: boolean;
  root?: boolean;
  theme?: Theme;
}

const fallbackIconIds = [
  "docker",
  "file",
  "folder",
  "folder-open",
  "folder-root",
  "folder-root-open",
  "folder-src",
  "folder-src-open",
  "git",
  "markdown",
  "nodejs",
  "pdf",
  "react_ts",
  "readme",
  "typescript",
];

const FALLBACK_MANIFEST: MaterialIconManifest = {
  iconDefinitions: Object.fromEntries(
    fallbackIconIds.map((id) => [id, { iconPath: `./../icons/${id}.svg` }]),
  ),
  fileExtensions: {
    md: "markdown",
    pdf: "pdf",
    ts: "typescript",
    tsx: "react_ts",
  },
  fileNames: {
    ".gitignore": "git",
    "package.json": "nodejs",
    "readme.md": "readme",
    dockerfile: "docker",
  },
  folderNames: { src: "folder-src" },
  folderNamesExpanded: { src: "folder-src-open" },
  rootFolderNames: {},
  rootFolderNamesExpanded: {},
  file: "file",
  folder: "folder",
  folderExpanded: "folder-open",
  rootFolder: "folder-root",
  rootFolderExpanded: "folder-root-open",
};

let activeManifest = FALLBACK_MANIFEST;
let manifestRequest: Promise<MaterialIconManifest> | null = null;

function isMaterialIconManifest(value: unknown): value is MaterialIconManifest {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<MaterialIconManifest>;
  return Boolean(
    candidate.iconDefinitions &&
    candidate.fileExtensions &&
    candidate.fileNames &&
    candidate.folderNames &&
    candidate.file &&
    candidate.folder,
  );
}

function loadMaterialIconManifest() {
  if (import.meta.env.MODE === "test") return Promise.resolve(activeManifest);
  if (manifestRequest) return manifestRequest;

  manifestRequest = fetch(materialIconManifestUrl)
    .then(async (response) => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const candidate: unknown = await response.json();
      if (!isMaterialIconManifest(candidate)) {
        throw new Error("Invalid Material Icon Theme manifest");
      }
      activeManifest = candidate;
      return candidate;
    })
    .catch((error: unknown) => {
      console.warn(
        "Material file icons could not be loaded; using fallbacks.",
        error,
      );
      return FALLBACK_MANIFEST;
    });
  return manifestRequest;
}

/** Load the complete mapping only while the explorer is mounted. */
export function useMaterialFileIcons() {
  const [, setManifest] = useState(activeManifest);

  useEffect(() => {
    let mounted = true;
    void loadMaterialIconManifest().then((manifest) => {
      if (mounted) setManifest(manifest);
    });
    return () => {
      mounted = false;
    };
  }, []);
}

function valueFor(map: Record<string, string> | undefined, key: string) {
  return map?.[key] ?? map?.[key.toLowerCase()];
}

function themedValue(
  base: Record<string, string>,
  light: Record<string, string> | undefined,
  key: string,
  theme: Theme,
) {
  return theme === "light"
    ? (valueFor(light, key) ?? valueFor(base, key))
    : valueFor(base, key);
}

function resolveFileIconId(
  manifest: MaterialIconManifest,
  name: string,
  path: string,
  theme: Theme,
) {
  const normalizedPath = path.replaceAll("\\", "/").replace(/^\.\//, "");
  const light = manifest.light;

  for (const candidate of [normalizedPath, name]) {
    const match = themedValue(
      manifest.fileNames,
      light?.fileNames,
      candidate,
      theme,
    );
    if (match) return match;
  }

  const extensionSource = name.startsWith(".") ? name.slice(1) : name;
  const parts = extensionSource.split(".");
  const firstExtensionPart = name.startsWith(".") ? 0 : 1;
  for (let index = firstExtensionPart; index < parts.length; index += 1) {
    const extension = parts.slice(index).join(".");
    const match = themedValue(
      manifest.fileExtensions,
      light?.fileExtensions,
      extension,
      theme,
    );
    if (match) return match;
  }

  return manifest.file;
}

function resolveFolderIconId(
  manifest: MaterialIconManifest,
  name: string,
  open: boolean,
  root: boolean,
  theme: Theme,
) {
  const light = manifest.light;
  const baseNames = root
    ? open
      ? manifest.rootFolderNamesExpanded
      : manifest.rootFolderNames
    : open
      ? manifest.folderNamesExpanded
      : manifest.folderNames;
  const lightNames = root
    ? open
      ? light?.rootFolderNamesExpanded
      : light?.rootFolderNames
    : open
      ? light?.folderNamesExpanded
      : light?.folderNames;

  return (
    themedValue(baseNames, lightNames, name, theme) ??
    (root
      ? open
        ? manifest.rootFolderExpanded
        : manifest.rootFolder
      : open
        ? manifest.folderExpanded
        : manifest.folder)
  );
}

function iconUrl(manifest: MaterialIconManifest, iconId: string) {
  const definition =
    manifest.iconDefinitions[iconId] ?? manifest.iconDefinitions[manifest.file];
  const fileName = definition.iconPath.slice(
    definition.iconPath.lastIndexOf("/") + 1,
  );
  return `/assets/material-file-icons/${fileName}`;
}

export function materialFileIcon(
  {
    name,
    path = name,
    directory = false,
    open = false,
    root = false,
    theme = "dark",
  }: MaterialFileIconOptions,
  manifest: MaterialIconManifest = activeManifest,
): MaterialFileIcon {
  const id = directory
    ? resolveFolderIconId(manifest, name, open, root, theme)
    : resolveFileIconId(manifest, name, path, theme);
  return { id, src: iconUrl(manifest, id) };
}
