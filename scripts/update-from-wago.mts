#!/usr/bin/env zx

import {
  exportJournalEncounterImages,
  exportJournalImages,
  exportLoadscreens,
  verifyStorageAccess,
} from "./wago-update/assets.mts";
import { parseCliOptions } from "./wago-update/cli.mts";
import {
  dryRunTables,
  listTrackedTables,
  updateTables,
} from "./wago-update/tables.mts";
import { latestBuild } from "./wago-update/wago.mts";

const argv = process.argv.slice(2);
if (argv[0]?.endsWith("update-from-wago.mts")) {
  argv.shift();
}
if (argv[0] === "--") {
  argv.shift();
}

const args = parseCliOptions(argv);

if (args.limit !== undefined && !args.dryRun) {
  throw new Error(
    "--limit is only supported with --dry-run to avoid partial data updates",
  );
}
if (args.skipTables && args.skipAssets) {
  throw new Error("--skip-tables and --skip-assets cannot be used together");
}
if (args.skipAssets && args.assetKind !== "all") {
  throw new Error("--asset-kind cannot be used with --skip-assets");
}
if (args.skipTables && args.dryRun) {
  throw new Error("--skip-tables cannot be used with --dry-run");
}

const build = args.version
  ? undefined
  : await latestBuild(args.product, args.timeout);
const version = args.version ?? build!.version;

let tables: string[] = [];
if (!args.skipTables) {
  tables = await listTrackedTables();
  if (args.limit !== undefined) {
    tables = tables.slice(0, Math.max(0, args.limit));
  }
  if (!tables.length) {
    throw new Error("No tables selected");
  }
}

console.log(`Wago product: ${args.product}`);
console.log(`Wago version: ${version}`);
if (build?.created_at) {
  console.log(`Build created: ${build.created_at}`);
}
console.log(`Tables: ${args.skipTables ? "skipped" : tables.length}`);

if (args.dryRun) {
  await dryRunTables(tables, version, args);
} else {
  if (!args.skipAssets) {
    await verifyStorageAccess(args);
  }
  if (!args.skipTables) {
    await updateTables(tables, version, build, args);
  }
  if (!args.skipAssets) {
    if (args.assetKind === "all" || args.assetKind === "journal") {
      await exportJournalImages(version, args);
    }
    if (args.assetKind === "journal-encounters") {
      await exportJournalEncounterImages(version, args);
    }
    if (args.assetKind === "all" || args.assetKind === "loadscreens") {
      await exportLoadscreens(version, args);
    }
  }
}
