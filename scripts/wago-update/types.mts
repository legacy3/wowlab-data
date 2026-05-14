export type BuildInfo = {
  product: string;
  version: string;
  created_at?: string;
  build_config?: string;
  product_config?: string;
  cdn_config?: string;
  is_bgdl?: boolean;
};

export type CliOptions = {
  product: string;
  version?: string;
  workers: number;
  timeout: number;
  attempts: number;
  force: boolean;
  dryRun: boolean;
  limit?: number;
  assetLimit?: number;
  skipAssets: boolean;
  assetWorkers: number;
  assetTimeout: number;
  journalOutput: string;
  loadscreenOutput: string;
};

export type JobResult = {
  name: string;
  ok: boolean;
  bytes?: number;
  error?: string;
};

export type ImageAsset = {
  fileDataId: number;
  sourceUrl: string;
  outputPath: string;
  width: number;
  height: number;
  bytes: number;
  status: "cached" | "downloaded";
};

export type ImageVariant = {
  path: string;
  width: number;
  height: number;
  bytes: number;
  publicUrl: string | null;
};

export type JournalSlot = "background" | "button" | "lore";

export type JournalInstance = {
  journalInstanceId: number;
  name: string;
  mapId?: number;
  backgroundFileDataId?: number;
  buttonFileDataId?: number;
  loreFileDataId?: number;
};

export type JournalAssetReference = {
  journalInstanceId: number;
  journalInstanceName: string;
  slot: JournalSlot;
};

export type JournalAssetExport = JobResult &
  ImageAsset & {
    ok: true;
    fileName?: string;
    filePath?: string;
    references: JournalAssetReference[];
  };

export type FailedExport = JobResult & { ok: false };

export type LoadscreenCandidate = {
  fileDataId: number;
  filename: string;
  stem: string;
  key: string;
  category: "dungeon" | "raid" | "zone" | "other";
};

export type LoadscreenExport = JobResult &
  LoadscreenCandidate & {
    ok: true;
    sourceUrl: string;
    full: ImageVariant;
    seo: ImageVariant;
  };

export type HeaderChanges = Record<
  string,
  | { status: "added"; columns: string[] }
  | { status: "removed" }
  | { status: "modified"; added: string[]; removed: string[] }
>;
