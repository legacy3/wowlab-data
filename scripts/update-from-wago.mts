#!/usr/bin/env zx

import { parseCliOptions } from "./wago-update/cli.mts";
import {
  exportJournalImages,
  exportLoadscreens,
} from "./wago-update/assets.mts";
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

const build = args.version
  ? undefined
  : await latestBuild(args.product, args.timeout);
const version = args.version ?? build!.version;

let tables = await listTrackedTables();
if (args.limit !== undefined) {
  tables = tables.slice(0, Math.max(0, args.limit));
}
if (!tables.length) {
  throw new Error("No tables selected");
}

console.log(`Wago product: ${args.product}`);
console.log(`Wago version: ${version}`);
if (build?.created_at) {
  console.log(`Build created: ${build.created_at}`);
}
console.log(`Tables: ${tables.length}`);

if (args.dryRun) {
  await dryRunTables(tables, version, args);
} else {
  await updateTables(tables, version, build, args);
  if (!args.skipAssets) {
    await exportJournalImages(version, args);
    await exportLoadscreens(version, args);
  }
}
