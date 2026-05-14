import { path } from "zx";

export const repoDir = path.resolve(
  path.dirname(new URL(import.meta.url).pathname),
  "..",
  "..",
);
export const dataDir = path.join(repoDir, "data", "tables");
export const changesDir = path.join(repoDir, "changes");
export const metadataDir = path.join(changesDir, "metadata");
