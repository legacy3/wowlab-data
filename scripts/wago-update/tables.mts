import { $, fs, os, path } from "zx";
import pRetry from "p-retry";
import type {
  BuildInfo,
  CliOptions,
  HeaderChanges,
  JobResult,
} from "./types.mts";
import { changesDir, dataDir, metadataDir } from "./paths.mts";
import { parseCsvRows } from "./csv.mts";
import { failed, runPool } from "./pool.mts";
import {
  fetchText,
  tableUrl,
  WAGO_BUILDS_URL,
  WAGO_DB2_CSV_URL,
} from "./wago.mts";

export async function listTrackedTables(): Promise<string[]> {
  const files = await listTrackedTableFiles();
  return files.map((file) => path.basename(file, ".csv")).sort();
}

async function listTrackedTableFiles(): Promise<string[]> {
  const tracked = await $`git ls-files "data/tables/*.csv"`;
  return tracked.stdout
    .split(/\r?\n/)
    .filter((file) => file.endsWith(".csv"))
    .sort();
}

async function readHeadHeaders(): Promise<Record<string, string[]>> {
  const out: Record<string, string[]> = {};
  for (const file of await listTrackedTableFiles()) {
    const text = (await $`git show ${`HEAD:${file}`}`).stdout;
    const first = parseCsvRows(text)[0];
    if (first?.length) {
      out[path.basename(file, ".csv")] = first;
    }
  }
  return out;
}

async function readHeaders(dir: string): Promise<Record<string, string[]>> {
  const out: Record<string, string[]> = {};
  if (!(await fs.pathExists(dir))) {
    return out;
  }

  for (const file of (await fs.readdir(dir))
    .filter((x: string) => x.endsWith(".csv"))
    .sort()) {
    const text = await fs.readFile(path.join(dir, file), "utf8");
    const first = parseCsvRows(text)[0];
    if (first?.length) {
      out[path.basename(file, ".csv")] = first;
    }
  }
  return out;
}

function generateDiff(
  oldHeaders: Record<string, string[]>,
  newHeaders: Record<string, string[]>,
): HeaderChanges {
  const changes: HeaderChanges = {};
  const tables = [
    ...new Set([...Object.keys(oldHeaders), ...Object.keys(newHeaders)]),
  ].sort();

  for (const table of tables) {
    const oldH = oldHeaders[table];
    const newH = newHeaders[table];
    if (!oldH && newH) {
      changes[table] = { status: "added", columns: newH };
    } else if (oldH && !newH) {
      changes[table] = { status: "removed" };
    } else if (oldH && newH && oldH.join("\0") !== newH.join("\0")) {
      const oldSet = new Set(oldH);
      const newSet = new Set(newH);
      changes[table] = {
        status: "modified",
        added: newH.filter((x) => !oldSet.has(x)),
        removed: oldH.filter((x) => !newSet.has(x)),
      };
    }
  }
  return changes;
}

async function writeMarkdown(changes: HeaderChanges, version: string) {
  const lines = [`# ${version} Structure Changes`, ""];
  const entries = Object.entries(changes).sort(([a], [b]) =>
    a.localeCompare(b),
  );
  if (!entries.length) {
    lines.push("No structure changes detected.");
  }

  for (const [table, change] of entries) {
    if (change.status === "added") {
      lines.push(`## ${table} (NEW TABLE)`, "", "**Columns:**");
      lines.push(...change.columns.map((col) => `- \`${col}\``), "");
    } else if (change.status === "removed") {
      lines.push(`## ${table} (REMOVED)`, "");
    } else {
      lines.push(`## ${table}`, "");
      if (change.added.length) {
        lines.push(
          "**Added:**",
          ...change.added.map((col) => `- \`${col}\``),
          "",
        );
      }
      if (change.removed.length) {
        lines.push(
          "**Removed:**",
          ...change.removed.map((col) => `- \`${col}\``),
          "",
        );
      }
      if (!change.added.length && !change.removed.length) {
        lines.push("Column order changed.", "");
      }
    }
  }

  await fs.ensureDir(changesDir);
  await fs.writeFile(
    path.join(changesDir, `${version}.md`),
    lines.join("\n"),
    "utf8",
  );
}

function validateCsv(table: string, text: string) {
  const prefix = text.slice(0, 512);
  const first = prefix.split(/\r?\n/)[0] ?? "";
  if (!first.trim()) {
    throw new Error("empty response");
  }
  if (
    prefix.includes("Wago Tools requires") ||
    prefix.toLowerCase().includes("<!doctype html")
  ) {
    throw new Error("received HTML instead of CSV");
  }
  if (
    table.toLowerCase() === "journalencounter" &&
    !first.includes("Name_lang")
  ) {
    throw new Error("unexpected JournalEncounter header");
  }
}

async function downloadTable(
  table: string,
  dest: string,
  version: string,
  args: CliOptions,
): Promise<JobResult> {
  try {
    return await pRetry(
      async () => {
        const text = await fetchText(tableUrl(table, version), args.timeout);
        validateCsv(table, text);
        const finalText = text.endsWith("\n") ? text : `${text}\n`;
        await fs.writeFile(path.join(dest, `${table}.csv`), finalText, "utf8");
        return { name: table, ok: true, bytes: Buffer.byteLength(finalText) };
      },
      {
        retries: Math.max(1, args.attempts) - 1,
        minTimeout: 2_000,
        maxTimeout: 10_000,
      },
    );
  } catch (err) {
    return {
      name: table,
      ok: false,
      error: err instanceof Error ? err.message : String(err),
    };
  }
}

export async function updateTables(
  tables: string[],
  version: string,
  build: BuildInfo | undefined,
  args: CliOptions,
) {
  const existingChange = path.join(changesDir, `${version}.md`);
  if (!args.force && (await fs.pathExists(existingChange))) {
    console.log(
      `Already have changes/${version}.md; use --force to rebuild it.`,
    );
    return;
  }

  const oldHeaders = await readHeadHeaders();
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), "wowlab-wago-"));
  const staging = path.join(tmp, "tables");
  await fs.ensureDir(staging);

  console.log("Downloading CSVs...");
  const results = await runPool(tables, args.workers, (table) =>
    downloadTable(table, staging, version, args),
  );
  const failures = results.filter(failed);
  const successes = results.filter((x) => x.ok);

  if (failures.length) {
    console.log("Download failures:");
    for (const failure of failures.slice(0, 50)) {
      console.log(`  ${failure.name}: ${failure.error}`);
    }
    throw new Error(
      `Aborting without replacing data/tables: ${failures.length} table downloads failed`,
    );
  }

  const changes = generateDiff(oldHeaders, await readHeaders(staging));
  await fs.remove(dataDir);
  await fs.move(staging, dataDir);
  await fs.remove(tmp);
  await writeMarkdown(changes, version);

  await fs.ensureDir(metadataDir);
  await fs.writeJson(
    path.join(metadataDir, `${version}.json`),
    {
      source: "wago.tools",
      product: args.product,
      version,
      build,
      tablesRequested: tables.length,
      tablesDownloaded: successes.length,
      tablesFailed: failures.length,
      failures,
      wago: {
        buildsUrl: WAGO_BUILDS_URL,
        db2CsvUrlTemplate: `${WAGO_DB2_CSV_URL}/<table>/csv?version=<build>`,
      },
    },
    { spaces: 2 },
  );

  console.log(`Updated data/tables with ${successes.length} tables`);
  console.log(`Structure changes: changes/${version}.md`);
  console.log(`Update metadata: changes/metadata/${version}.json`);
  console.log(`Tables with structure changes: ${Object.keys(changes).length}`);
}

export async function dryRunTables(
  tables: string[],
  version: string,
  args: CliOptions,
) {
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), "wowlab-wago-dry-run-"));
  await fs.ensureDir(tmp);

  console.log("Dry run downloading CSVs...");
  const results = await runPool(tables, args.workers, (table) =>
    downloadTable(table, tmp, version, args),
  );
  const failures = results.filter(failed);

  console.log(`Dry run downloaded: ${results.length - failures.length}`);
  console.log(`Dry run failures: ${failures.length}`);
  for (const failure of failures.slice(0, 50)) {
    console.log(`  ${failure.name}: ${failure.error}`);
  }

  await fs.remove(tmp);
  if (failures.length) {
    process.exit(1);
  }
}
