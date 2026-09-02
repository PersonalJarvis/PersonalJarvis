#!/usr/bin/env node
/**
 * File suggestions for Claude Code's `@` picker (settings.json `fileSuggestion`).
 *
 * WHY THIS EXISTS. The built-in picker lists what `git ls-files` returns, so a
 * git-ignored folder is invisible to `@` even though it is right there on
 * disk. `personaljarvisweb/` is ignored ON PURPOSE (see .gitignore: it is its
 * own repository and must never be staged from here), and it must still be
 * reachable by `@personaljarvisweb/...`. Turning `respectGitignore` off is not
 * an option: it swaps git for a raw ripgrep walk that surfaces ~200k ignored
 * files (node_modules, data, venvs). This script keeps git as the source of
 * truth and simply asks the nested repository for its own list.
 *
 * CONTRACT (Claude Code, verified against 2.1.x): the command receives a JSON
 * object on stdin with at least `query` and `cwd`, and prints one path per
 * line on stdout, relative to the project root, already filtered and ranked
 * for that query. Claude Code does no matching of its own on top. It aborts
 * the command after five seconds, so everything here is `git ls-files`, which
 * answers in well under a second on this tree.
 *
 * Runs on Windows, macOS and Linux with nothing but node and git.
 */
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";

/** Git repositories nested inside this checkout that the parent ignores. */
const NESTED_REPOS = ["personaljarvisweb"];

const MAX_RESULTS = 100;

function readStdin() {
  try {
    const raw = readFileSync(0, "utf8");
    return raw.trim() ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

function gitFiles(repoDir) {
  const run = (args) =>
    execFileSync("git", ["-C", repoDir, "-c", "core.quotePath=false", "ls-files", ...args], {
      encoding: "utf8",
      maxBuffer: 64 * 1024 * 1024,
      windowsHide: true,
      stdio: ["ignore", "pipe", "ignore"],
    })
      .split("\n")
      .filter(Boolean);
  return [...run([]), ...run(["--others", "--exclude-standard"])];
}

/** All files plus every directory that leads to one, so `@folder` matches too. */
function collect(root) {
  const entries = new Set();
  const add = (prefix, paths) => {
    for (const p of paths) {
      const rel = prefix ? `${prefix}/${p}` : p;
      entries.add(rel);
      let cut = rel.lastIndexOf("/");
      while (cut > 0) {
        entries.add(rel.slice(0, cut) + "/");
        cut = rel.lastIndexOf("/", cut - 1);
      }
    }
  };
  add("", gitFiles(root));
  for (const nested of NESTED_REPOS) {
    const dir = join(root, nested);
    if (!existsSync(join(dir, ".git"))) continue;
    try {
      add(nested, gitFiles(dir));
    } catch {
      // The nested clone is optional; a checkout without it simply lists less.
    }
  }
  return [...entries];
}

/**
 * Rank a path for a query. Higher is better; null means no match.
 * Substring beats subsequence, a hit on the file name beats one deep in the
 * path, and shorter paths win ties so the obvious file comes first.
 */
function score(path, query) {
  const hay = path.toLowerCase();
  const name = hay.slice(hay.lastIndexOf("/", hay.length - 2) + 1);
  if (name.startsWith(query)) return 400 - path.length;
  if (hay.startsWith(query)) return 300 - path.length;
  if (name.includes(query)) return 200 - path.length;
  if (hay.includes(query)) return 100 - path.length;
  let at = 0;
  for (const ch of query) {
    at = hay.indexOf(ch, at);
    if (at < 0) return null;
    at += 1;
  }
  return -path.length;
}

function main() {
  const input = readStdin();
  const root = resolve(process.env.CLAUDE_PROJECT_DIR || input.cwd || process.cwd());
  const query = String(input.query ?? "")
    .replace(/\\/g, "/")
    .replace(/^\.\//, "")
    .toLowerCase();

  const entries = collect(root);
  const ranked = query
    ? entries
        .map((path) => [path, score(path, query)])
        .filter(([, s]) => s !== null)
        .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
        .map(([path]) => path)
    : entries.filter((path) => !path.slice(0, -1).includes("/")).sort();

  process.stdout.write(ranked.slice(0, MAX_RESULTS).join("\n") + "\n");
}

main();
