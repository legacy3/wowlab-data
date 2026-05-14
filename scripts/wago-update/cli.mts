import { Command, Option } from "commander";

import type { CliOptions } from "./types.mts";

export function parseCliOptions(argv: string[]): CliOptions {
  const program = new Command()
    .name("update-from-wago")
    .description("Update all WoW DB2 CSV data and derived Wago image assets.")
    .showHelpAfterError()
    .addHelpText(
      "after",
      `
Examples:
  pnpm refresh:dry-run
  pnpm refresh
  pnpm refresh -- --version 12.0.5.67451
  pnpm refresh -- --skip-assets
  pnpm refresh -- --skip-tables`,
    )
    .option("--product <name>", "Wago product branch", "wow")
    .option(
      "--version <build>",
      "Exact Wago build; defaults to latest for product",
    )
    .option("--workers <n>", "Table download concurrency", integer, 4)
    .option(
      "--timeout <seconds>",
      "HTTP timeout for Wago requests",
      integer,
      180,
    )
    .option("--attempts <n>", "Attempts per table download", integer, 4)
    .option("--force", "Rebuild even if changes/<version>.md exists", false)
    .option(
      "--dry-run",
      "Download all tables to a temp dir and validate only",
      false,
    )
    .addOption(
      new Option("--limit <n>", "Limit table count for tests")
        .argParser(integer)
        .hideHelp(),
    )
    .option(
      "--skip-assets",
      "Only update CSV tables; skip image asset exports",
      false,
    )
    .option(
      "--skip-tables",
      "Only export image assets; skip CSV table downloads and structure diff",
      false,
    )
    .option("--asset-workers <n>", "Asset concurrency", integer, 4)
    .option(
      "--asset-timeout <seconds>",
      "HTTP timeout for asset requests",
      integer,
      300,
    )
    .addOption(
      new Option("--asset-limit <n>", "Limit asset count for tests")
        .argParser(integer)
        .hideHelp(),
    )
    .option(
      "--supabase-bucket <name>",
      "Supabase storage bucket",
      "encounter-images",
    )
    .addOption(
      new Option("--log-level <level>", "Reserved for future logging control")
        .choices(["quiet", "info", "debug"])
        .default("info"),
    );

  program.parse(argv, { from: "user" });
  return program.opts<CliOptions>();
}

function integer(value: string): number {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed) || parsed < 0) {
    throw new Error(`Expected a non-negative integer, got ${value}`);
  }
  return parsed;
}
