import { copyFile, mkdir, open, readFile, readdir, rename, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { randomUUID } from "node:crypto";

export const ASSET_GRACE_MS = 24 * 60 * 60 * 1000;

async function filesIn(directory, prefix = "") {
  const files = [];
  for (const entry of await readdir(path.join(directory, prefix), { withFileTypes: true })) {
    const name = path.join(prefix, entry.name);
    if (entry.isDirectory()) files.push(...await filesIn(directory, name));
    else if (entry.isFile()) files.push(name);
    else throw new Error(`Unexpected non-file build output: ${name}`);
  }
  return files;
}

async function replaceFile(source, destination) {
  await mkdir(path.dirname(destination), { recursive: true });
  const temporary = `${destination}.${randomUUID()}.tmp`;
  try {
    await copyFile(source, temporary);
    // Readers see either complete file; never unlink the live destination.
    await rename(temporary, destination);
  } finally {
    await rm(temporary, { force: true });
  }
}

/** Publish dependencies first and the entry document last, without emptying dist. */
export async function publishBuild(staging, destination, now = Date.now()) {
  const lockPath = path.join(path.dirname(destination), ".frontend-publish.lock");
  await mkdir(path.dirname(destination), { recursive: true });
  // Concurrent builders may compile independently, but publication and asset
  // retirement must never interleave. A refused publisher leaves dist intact.
  let lock;
  try {
    lock = await open(lockPath, "wx");
  } catch (error) {
    if (error.code === "EEXIST") {
      throw new Error("Another frontend build is publishing. Retry after it finishes; if it crashed, remove .frontend-publish.lock.");
    }
    throw error;
  }
  try {
    await publishLocked(staging, destination, now);
  } finally {
    await lock.close();
    await rm(lockPath, { force: true });
  }
}

async function publishLocked(staging, destination, now) {
  const files = await filesIn(staging);
  if (!files.includes("index.html")) throw new Error("Build has no entry document");
  const html = await readFile(path.join(staging, "index.html"), "utf8");
  const names = new Set(files.map((name) => name.split(path.sep).join("/")));
  const references = html.match(/\/assets\/[A-Za-z0-9._-]+/g) ?? [];
  if (!references.length || references.some((name) => !names.has(name.slice(1)))) {
    throw new Error("Build entry references missing assets");
  }
  for (const file of files.filter((name) => name !== "index.html")) {
    await replaceFile(path.join(staging, file), path.join(destination, file));
  }
  await replaceFile(path.join(staging, "index.html"), path.join(destination, "index.html"));

  // Start the grace period when an asset is retired, not at its original build
  // time. Even a month-old window can finish a call across today's first build.
  const ledgerPath = path.join(path.dirname(destination), ".frontend-retired-assets.json");
  let retired = {};
  try {
    retired = JSON.parse(await readFile(ledgerPath, "utf8"));
    if (!retired || typeof retired !== "object" || Array.isArray(retired)) retired = {};
  } catch (error) {
    if (error.code !== "ENOENT") console.warn("Resetting unreadable retired-asset ledger", error);
  }
  const next = {};
  for (const file of await readdir(path.join(destination, "assets"), { withFileTypes: true })) {
    // Only generated hashed files; public assets and nested directories stay.
    if (!file.isFile() || !/-[A-Za-z0-9_-]{8}\.[a-z0-9]+$/.test(file.name)) continue;
    if (names.has(`assets/${file.name}`)) continue;
    const since = Number.isFinite(retired[file.name]) ? retired[file.name] : now;
    if (now - since >= ASSET_GRACE_MS) {
      await rm(path.join(destination, "assets", file.name), { force: true });
    } else {
      next[file.name] = since;
    }
  }
  const temporaryLedger = `${ledgerPath}.${randomUUID()}.tmp`;
  try {
    await writeFile(temporaryLedger, JSON.stringify(next));
    await rename(temporaryLedger, ledgerPath);
  } finally {
    await rm(temporaryLedger, { force: true });
  }
}
