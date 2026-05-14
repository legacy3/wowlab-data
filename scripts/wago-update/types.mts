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
  skipTables: boolean;
  skipAssets: boolean;
  assetWorkers: number;
  assetTimeout: number;
  assetJobTimeout: number;
  assetKind: AssetKind;
  supabaseBucket: string;
};

export type AssetKind =
  | "all"
  | "journal"
  | "journal-encounters"
  | "loadscreens";

export type FailedExport = { ok: false } & JobResult;

export type HeaderChanges = Record<
  string,
  | { status: "added"; columns: string[] }
  | { status: "removed" }
  | { status: "modified"; added: string[]; removed: string[] }
>;

export type ImageAsset = {
  fileDataId: number;
  sourceUrl: string;
  storagePath: string;
  width: number;
  height: number;
  bytes: number;
  status: "exists" | "uploaded";
};

export type ImageVariant = {
  path: string;
  width: number;
  height: number;
  bytes: number;
  publicUrl: string | null;
};

export type JobResult = {
  name: string;
  ok: boolean;
  bytes?: number;
  error?: string;
};

export type JournalAssetExport = {
  ok: true;
  fileName?: string;
  filePath?: string;
  references: JournalAssetReference[];
} & ImageAsset &
  JobResult;

export type JournalAssetReference = {
  journalInstanceId: number;
  journalInstanceName: string;
  slot: JournalSlot;
};

export type JournalInstance = {
  journalInstanceId: number;
  name: string;
  mapId?: number;
  backgroundFileDataId?: number;
  buttonFileDataId?: number;
  loreFileDataId?: number;
};

export type JournalSlot = "background" | "button" | "lore";

export type LoadscreenCandidate = {
  fileDataId: number;
  filename: string;
  stem: string;
  key: string;
  category: "dungeon" | "raid" | "zone" | "other";
};

export type LoadscreenExport = {
  ok: true;
  sourceUrl: string;
  full: ImageVariant;
  seo: ImageVariant;
} & JobResult &
  LoadscreenCandidate;
