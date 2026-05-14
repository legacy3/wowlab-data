import { createRequire } from "node:module";
import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import pRetry from "p-retry";
import sharp from "sharp";
import type {
  CliOptions,
  FailedExport,
  ImageAsset,
  ImageVariant,
  JournalAssetExport,
  JournalAssetReference,
  JournalInstance,
  JournalSlot,
  LoadscreenCandidate,
  LoadscreenExport,
} from "./types.mts";
import { dataDir } from "./paths.mts";
import { loadCsvObjects, parsePositiveInt } from "./csv.mts";
import { failed, failure, ok, runPool } from "./pool.mts";
import { cascUrl, fetchBytes, WAGO_CASC_URL } from "./wago.mts";
import { path } from "zx";

const require = createRequire(import.meta.url);
const BLPFile = require("js-blp");
const SEO_RATIO = 2992 / 1148;

type PngImage = {
  bytes: Buffer;
  width: number;
  height: number;
};

type StorageContext = {
  client: SupabaseClient;
  bucket: string;
  existing: Map<string, Set<string>>;
};

function normalizeKey(raw: string) {
  let value = raw.toLowerCase().replace(/\.blp$/i, "");
  for (const prefix of [
    "ui-ej-dungeonbutton-",
    "ui-ej-background-",
    "ui-ej-lorebg-",
    "loadscreen_dungeon_",
    "loadscreen_raid_",
    "loadscreen_zone_",
    "loadscreen_",
    "loadingscreen_",
    "loadscreen",
    "loadingscreen",
  ]) {
    if (value.startsWith(prefix)) {
      value = value.slice(prefix.length);
    }
  }
  return value.replace(/[^a-z0-9]+/g, "");
}

function storageClient(): SupabaseClient {
  const url = process.env.SUPABASE_URL;
  const key =
    process.env.SUPABASE_SERVICE_ROLE_KEY ?? process.env.SUPABASE_SERVICE_KEY;
  if (!url || !key) {
    throw new Error(
      "Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY to upload assets",
    );
  }
  return createClient(url, key, { auth: { persistSession: false } });
}

function splitStoragePath(storagePath: string) {
  const slash = storagePath.lastIndexOf("/");
  return {
    dir: slash === -1 ? "" : storagePath.slice(0, slash),
    file: slash === -1 ? storagePath : storagePath.slice(slash + 1),
  };
}

async function listExisting(
  ctx: StorageContext,
  dir: string,
): Promise<Set<string>> {
  const cached = ctx.existing.get(dir);
  if (cached) {
    return cached;
  }

  const files = new Set<string>();
  let offset = 0;
  for (;;) {
    const { data, error } = await ctx.client.storage
      .from(ctx.bucket)
      .list(dir, {
        limit: 1000,
        offset,
        sortBy: { column: "name", order: "asc" },
      });
    if (error) {
      throw new Error(`Unable to list ${ctx.bucket}/${dir}: ${error.message}`);
    }
    for (const item of data ?? []) {
      files.add(item.name);
    }
    if (!data || data.length < 1000) {
      break;
    }
    offset += data.length;
  }

  ctx.existing.set(dir, files);
  return files;
}

async function objectExists(ctx: StorageContext, storagePath: string) {
  const { dir, file } = splitStoragePath(storagePath);
  return (await listExisting(ctx, dir)).has(file);
}

async function uploadBuffer(
  ctx: StorageContext,
  storagePath: string,
  body: Buffer | string,
  contentType: string,
  upsert: boolean,
) {
  const { error } = await ctx.client.storage
    .from(ctx.bucket)
    .upload(storagePath, body, {
      contentType,
      upsert,
    });
  if (error) {
    throw new Error(
      `Unable to upload ${ctx.bucket}/${storagePath}: ${error.message}`,
    );
  }

  const { dir, file } = splitStoragePath(storagePath);
  const existing = ctx.existing.get(dir);
  if (existing) {
    existing.add(file);
  }
}

function publicUrl(ctx: StorageContext, storagePath: string) {
  return ctx.client.storage.from(ctx.bucket).getPublicUrl(storagePath).data
    .publicUrl;
}

async function blpToPng(raw: Buffer): Promise<PngImage> {
  const blp = new BLPFile(raw);
  const pixels = blp.getPixels(0);
  const pixelBuffer = Buffer.from(pixels.raw ?? pixels._buffer ?? pixels);
  const { data, info } = await sharp(pixelBuffer, {
    raw: { width: blp.width, height: blp.height, channels: 4 },
  })
    .png()
    .toBuffer({ resolveWithObject: true });
  return { bytes: data, width: info.width, height: info.height };
}

async function fetchBlpAsPng(
  fdid: number,
  version: string,
  args: CliOptions,
): Promise<PngImage> {
  const sourceUrl = cascUrl(fdid, version);
  return await pRetry(
    async () => await blpToPng(await fetchBytes(sourceUrl, args.assetTimeout)),
    {
      retries: Math.max(1, args.attempts) - 1,
      minTimeout: 2_000,
      maxTimeout: 10_000,
    },
  );
}

async function uploadPngAsset(
  ctx: StorageContext,
  fdid: number,
  storagePath: string,
  version: string,
  args: CliOptions,
): Promise<{ asset: ImageAsset; png: PngImage }> {
  const sourceUrl = cascUrl(fdid, version);
  const png = await fetchBlpAsPng(fdid, version, args);
  const exists = await objectExists(ctx, storagePath);
  if (!exists) {
    await uploadBuffer(ctx, storagePath, png.bytes, "image/png", false);
  }
  return {
    asset: {
      fileDataId: fdid,
      sourceUrl,
      storagePath,
      width: png.width,
      height: png.height,
      bytes: png.bytes.length,
      status: exists ? "exists" : "uploaded",
    },
    png,
  };
}

function imageVariant(ctx: StorageContext, image: ImageAsset): ImageVariant {
  return {
    path: image.storagePath,
    width: image.width,
    height: image.height,
    bytes: image.bytes,
    publicUrl: publicUrl(ctx, image.storagePath),
  };
}

async function centerCropPng(input: Buffer): Promise<PngImage> {
  const metadata = await sharp(input).metadata();
  const width = metadata.width ?? 0;
  const height = metadata.height ?? 0;
  if (!width || !height) {
    throw new Error("Unable to read PNG dimensions");
  }

  const current = width / height;
  const cropW = current > SEO_RATIO ? Math.floor(height * SEO_RATIO) : width;
  const cropH = current > SEO_RATIO ? height : Math.floor(width / SEO_RATIO);
  const left = Math.floor((width - cropW) / 2);
  const top = Math.floor((height - cropH) / 2);
  const { data, info } = await sharp(input)
    .extract({ left, top, width: cropW, height: cropH })
    .png()
    .toBuffer({ resolveWithObject: true });
  return { bytes: data, width: info.width, height: info.height };
}

async function uploadSeoVariant(
  ctx: StorageContext,
  full: PngImage,
  storagePath: string,
): Promise<ImageVariant> {
  const seo = await centerCropPng(full.bytes);
  const exists = await objectExists(ctx, storagePath);
  if (!exists) {
    await uploadBuffer(ctx, storagePath, seo.bytes, "image/png", false);
  }
  return {
    path: storagePath,
    width: seo.width,
    height: seo.height,
    bytes: seo.bytes.length,
    publicUrl: publicUrl(ctx, storagePath),
  };
}

async function uploadJson(
  ctx: StorageContext,
  storagePath: string,
  body: unknown,
) {
  await uploadBuffer(
    ctx,
    storagePath,
    JSON.stringify(body, null, 2),
    "application/json",
    true,
  );
}

function collectJournalAssets(journalRows: Record<string, string>[]) {
  const instances: JournalInstance[] = [];
  const references = new Map<number, JournalAssetReference[]>();

  for (const row of journalRows) {
    const journalInstanceId = parsePositiveInt(row.ID);
    if (!journalInstanceId) {
      continue;
    }

    const instance: JournalInstance = {
      journalInstanceId,
      name: row.Name_lang ?? "",
      mapId: parsePositiveInt(row.MapID),
      backgroundFileDataId: parsePositiveInt(row.BackgroundFileDataID),
      buttonFileDataId: parsePositiveInt(row.ButtonFileDataID),
      loreFileDataId: parsePositiveInt(row.LoreFileDataID),
    };
    instances.push(instance);

    for (const [slot, key] of [
      ["background", "backgroundFileDataId"],
      ["button", "buttonFileDataId"],
      ["lore", "loreFileDataId"],
    ] as const satisfies readonly [JournalSlot, keyof JournalInstance][]) {
      const fdid = instance[key];
      if (typeof fdid !== "number") {
        continue;
      }
      references.set(fdid, [
        ...(references.get(fdid) ?? []),
        { journalInstanceId, journalInstanceName: instance.name, slot },
      ]);
    }
  }

  return { instances, references };
}

function storageContext(args: CliOptions): StorageContext {
  return {
    client: storageClient(),
    bucket: args.supabaseBucket,
    existing: new Map(),
  };
}

export async function exportJournalImages(version: string, args: CliOptions) {
  const ctx = storageContext(args);
  const imagesPrefix = "journal/images";

  const manifestRows = await loadCsvObjects(
    path.join(dataDir, "ManifestInterfaceData.csv"),
  );
  const manifest = new Map<number, { fileName: string; filePath: string }>();
  for (const row of manifestRows) {
    const id = parsePositiveInt(row.ID);
    if (id) {
      manifest.set(id, {
        fileName: row.FileName ?? "",
        filePath: row.FilePath ?? "",
      });
    }
  }

  const { instances, references } = collectJournalAssets(
    await loadCsvObjects(path.join(dataDir, "JournalInstance.csv")),
  );
  let fdids = [...references.keys()].sort((a, b) => a - b);
  if (args.assetLimit !== undefined) {
    fdids = fdids.slice(0, Math.max(0, args.assetLimit));
  }

  await listExisting(ctx, imagesPrefix);
  console.log(`Journal instances: ${instances.length}`);
  console.log(`Unique journal image IDs: ${fdids.length}`);

  const results = await runPool<number, JournalAssetExport | FailedExport>(
    fdids,
    args.assetWorkers,
    async (fdid) => {
      try {
        const { asset: image } = await uploadPngAsset(
          ctx,
          fdid,
          `${imagesPrefix}/${fdid}.png`,
          version,
          args,
        );
        const meta = manifest.get(fdid) ?? {
          fileName: undefined,
          filePath: undefined,
        };
        return {
          ...image,
          name: String(fdid),
          ok: true,
          bytes: image.bytes,
          ...meta,
          references: references.get(fdid) ?? [],
        };
      } catch (err) {
        return failure(fdid, err) as FailedExport;
      }
    },
  );

  const assets = results.filter(ok);
  const failures = results.filter(failed);
  const exportedById = new Map(
    assets.map((asset) => [asset.fileDataId, asset]),
  );
  const instancesById: Record<
    string,
    JournalInstance & Record<JournalSlot, ImageVariant | null>
  > = {};

  for (const instance of instances) {
    const out: JournalInstance & Record<JournalSlot, ImageVariant | null> = {
      ...instance,
      background: null,
      button: null,
      lore: null,
    };
    for (const [slot, key] of [
      ["background", "backgroundFileDataId"],
      ["button", "buttonFileDataId"],
      ["lore", "loreFileDataId"],
    ] as const satisfies readonly [JournalSlot, keyof JournalInstance][]) {
      const fdid = instance[key];
      const asset =
        typeof fdid === "number" ? exportedById.get(fdid) : undefined;
      if (asset) {
        out[slot] = imageVariant(ctx, asset);
      }
    }
    instancesById[String(instance.journalInstanceId)] = out;
  }

  const generatedAt = new Date().toISOString();
  const summary = {
    journalInstances: instances.length,
    requestedImages: fdids.length,
    uploadedImages: assets.filter((asset) => asset.status === "uploaded")
      .length,
    existingImages: assets.filter((asset) => asset.status === "exists").length,
    exportedImages: assets.length,
    failedImages: failures.length,
    coveragePercent:
      Math.round((assets.length / Math.max(1, fdids.length)) * 10000) / 100,
  };
  await uploadJson(ctx, "journal/manifest.json", {
    generatedAt,
    source: { assetUrlTemplate: `${WAGO_CASC_URL}/{fdid}`, version },
    summary,
    imagesPrefix,
    assets,
    failures,
  });
  await uploadJson(ctx, "journal/journal-instance-images.json", {
    generatedAt,
    summary,
    instancesById,
  });
  console.log(`Uploaded journal images: ${ctx.bucket}/${imagesPrefix}`);
  if (failures.length) {
    throw new Error(
      `Journal image export failed for ${failures.length} assets`,
    );
  }
}

function loadscreenCandidate(
  row: Record<string, string>,
): LoadscreenCandidate | null {
  const fileDataId = parsePositiveInt(row.ID);
  const fileName = row.FileName ?? "";
  const fullPath =
    `${(row.FilePath ?? "").replace(/\\/g, "/").replace(/\/$/, "")}/${fileName}`.replace(
      /^\//,
      "",
    );
  const stem = path.basename(fileName, ".blp");
  const lower = fullPath.toLowerCase();
  if (
    !fileDataId ||
    !lower.startsWith("interface/glues/loadingscreens/") ||
    !lower.endsWith(".blp")
  ) {
    return null;
  }
  if (
    !stem.toLowerCase().startsWith("loadscreen") &&
    !stem.toLowerCase().startsWith("loadingscreen")
  ) {
    return null;
  }

  return {
    fileDataId,
    filename: fullPath,
    stem,
    key: normalizeKey(stem),
    category: stem.toLowerCase().includes("dungeon")
      ? "dungeon"
      : stem.toLowerCase().includes("raid")
        ? "raid"
        : stem.toLowerCase().includes("zone")
          ? "zone"
          : "other",
  };
}

async function exportLoadscreen(
  ctx: StorageContext,
  candidate: LoadscreenCandidate,
  version: string,
  args: CliOptions,
): Promise<LoadscreenExport | FailedExport> {
  try {
    const { asset: image, png } = await uploadPngAsset(
      ctx,
      candidate.fileDataId,
      `loadscreens/images/${candidate.fileDataId}.png`,
      version,
      args,
    );
    return {
      ...candidate,
      name: String(candidate.fileDataId),
      ok: true,
      bytes: image.bytes,
      sourceUrl: cascUrl(candidate.fileDataId, version),
      full: imageVariant(ctx, image),
      seo: await uploadSeoVariant(
        ctx,
        png,
        `loadscreens/seo/${candidate.fileDataId}.png`,
      ),
    };
  } catch (err) {
    return {
      ...candidate,
      ...(failure(candidate.fileDataId, err) as FailedExport),
    };
  }
}

export async function exportLoadscreens(version: string, args: CliOptions) {
  const ctx = storageContext(args);
  await Promise.all([
    listExisting(ctx, "loadscreens/images"),
    listExisting(ctx, "loadscreens/seo"),
  ]);

  let candidates = (
    await loadCsvObjects(path.join(dataDir, "ManifestInterfaceData.csv"))
  )
    .map(loadscreenCandidate)
    .filter((candidate): candidate is LoadscreenCandidate => candidate !== null)
    .sort((a, b) => a.fileDataId - b.fileDataId);
  if (args.assetLimit !== undefined) {
    candidates = candidates.slice(0, Math.max(0, args.assetLimit));
  }

  console.log(`Loadscreen candidates: ${candidates.length}`);
  const exported = await runPool(candidates, args.assetWorkers, (candidate) =>
    exportLoadscreen(ctx, candidate, version, args),
  );
  const entries = exported.filter(ok);
  const failures = exported.filter(failed);

  await uploadJson(ctx, "loadscreens/manifest.json", {
    generatedAt: new Date().toISOString(),
    source: {
      indexSource: "ManifestInterfaceData.csv",
      assetUrlTemplate: `${WAGO_CASC_URL}/{fdid}`,
      version,
    },
    summary: {
      candidateCount: candidates.length,
      exportedCount: entries.length,
      uploadedFullImages: entries.filter(
        (entry) => entry.full && entry.full.path,
      ).length,
      failureCount: failures.length,
    },
    entries,
    failures,
  });
  console.log(`Uploaded loadscreens: ${ctx.bucket}/loadscreens`);
  if (failures.length) {
    throw new Error(`Loadscreen export failed for ${failures.length} assets`);
  }
}
