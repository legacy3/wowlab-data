import { createRequire } from "node:module";
import { fs, path } from "zx";
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
import { dataDir, repoDir } from "./paths.mts";
import { loadCsvObjects, parsePositiveInt } from "./csv.mts";
import { failed, failure, ok, runPool } from "./pool.mts";
import { cascUrl, fetchBytes, WAGO_CASC_URL } from "./wago.mts";

const require = createRequire(import.meta.url);
const BLPFile = require("js-blp");
const SEO_RATIO = 2992 / 1148;

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

async function blpToPng(
  raw: Buffer,
): Promise<{ bytes: Buffer; width: number; height: number }> {
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
  outputPath: string,
  version: string,
  args: CliOptions,
): Promise<ImageAsset> {
  const sourceUrl = cascUrl(fdid, version);
  if (await fs.pathExists(outputPath)) {
    const metadata = await sharp(outputPath).metadata();
    return {
      fileDataId: fdid,
      sourceUrl,
      outputPath,
      width: metadata.width ?? 0,
      height: metadata.height ?? 0,
      bytes: (await fs.stat(outputPath)).size,
      status: "cached",
    };
  }

  let error = "unknown error";
  for (let attempt = 1; attempt <= Math.max(1, args.attempts); attempt++) {
    try {
      const png = await blpToPng(
        await fetchBytes(sourceUrl, args.assetTimeout),
      );
      await fs.ensureDir(path.dirname(outputPath));
      await fs.writeFile(outputPath, png.bytes);
      return {
        fileDataId: fdid,
        sourceUrl,
        outputPath,
        width: png.width,
        height: png.height,
        bytes: png.bytes.length,
        status: "downloaded",
      };
    } catch (err) {
      error = err instanceof Error ? err.message : String(err);
      if (attempt < args.attempts) {
        await new Promise((resolve) =>
          setTimeout(resolve, Math.min(10, attempt * 2) * 1000),
        );
      }
    }
  }

  throw new Error(error);
}

function imageVariant(image: ImageAsset): ImageVariant {
  return {
    path: image.outputPath,
    width: image.width,
    height: image.height,
    bytes: image.bytes,
    publicUrl: null,
  };
}

async function pngVariant(filePath: string): Promise<ImageVariant> {
  const [metadata, stat] = await Promise.all([
    sharp(filePath).metadata(),
    fs.stat(filePath),
  ]);
  return {
    path: filePath,
    width: metadata.width ?? 0,
    height: metadata.height ?? 0,
    bytes: stat.size,
    publicUrl: null,
  };
}

async function centerCropPng(input: Buffer): Promise<Buffer> {
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
  return await sharp(input)
    .extract({ left, top, width: cropW, height: cropH })
    .png()
    .toBuffer();
}

async function ensureSeoVariant(
  sourcePath: string,
  seoPath: string,
): Promise<ImageVariant> {
  if (!(await fs.pathExists(seoPath))) {
    await fs.writeFile(
      seoPath,
      await centerCropPng(await fs.readFile(sourcePath)),
    );
  }
  return await pngVariant(seoPath);
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

export async function exportJournalImages(version: string, args: CliOptions) {
  const outputDir = path.resolve(repoDir, args.journalOutput);
  const imagesDir = path.join(outputDir, "images");
  await fs.ensureDir(imagesDir);

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

  console.log(`Journal instances: ${instances.length}`);
  console.log(`Unique journal image IDs: ${fdids.length}`);

  const results = await runPool<number, JournalAssetExport | FailedExport>(
    fdids,
    args.assetWorkers,
    async (fdid) => {
      try {
        const image = await fetchBlpAsPng(
          fdid,
          path.join(imagesDir, `${fdid}.png`),
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
        out[slot] = imageVariant(asset);
      }
    }
    instancesById[String(instance.journalInstanceId)] = out;
  }

  const generatedAt = new Date().toISOString();
  const summary = {
    journalInstances: instances.length,
    requestedImages: fdids.length,
    exportedImages: assets.length,
    failedImages: failures.length,
    coveragePercent:
      Math.round((assets.length / Math.max(1, fdids.length)) * 10000) / 100,
  };
  await fs.writeJson(
    path.join(outputDir, "manifest.json"),
    {
      generatedAt,
      source: { assetUrlTemplate: `${WAGO_CASC_URL}/{fdid}`, version },
      summary,
      imagesDir,
      assets,
      failures,
    },
    { spaces: 2 },
  );
  await fs.writeJson(
    path.join(outputDir, "journal-instance-images.json"),
    { generatedAt, summary, instancesById },
    { spaces: 2 },
  );
  console.log(`Wrote journal images: ${outputDir}`);
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
  candidate: LoadscreenCandidate,
  version: string,
  args: CliOptions,
  imagesDir: string,
  seoDir: string,
): Promise<LoadscreenExport | FailedExport> {
  try {
    const fullPath = path.join(imagesDir, `${candidate.fileDataId}.png`);
    const image = await fetchBlpAsPng(
      candidate.fileDataId,
      fullPath,
      version,
      args,
    );
    return {
      ...candidate,
      name: String(candidate.fileDataId),
      ok: true,
      bytes: image.bytes,
      sourceUrl: cascUrl(candidate.fileDataId, version),
      full: imageVariant(image),
      seo: await ensureSeoVariant(
        fullPath,
        path.join(seoDir, `${candidate.fileDataId}.png`),
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
  const outputDir = path.resolve(repoDir, args.loadscreenOutput);
  const imagesDir = path.join(outputDir, "images");
  const seoDir = path.join(outputDir, "seo");
  await fs.ensureDir(imagesDir);
  await fs.ensureDir(seoDir);

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
    exportLoadscreen(candidate, version, args, imagesDir, seoDir),
  );
  const entries = exported.filter(ok);
  const failures = exported.filter(failed);

  await fs.writeJson(
    path.join(outputDir, "manifest.json"),
    {
      generatedAt: new Date().toISOString(),
      source: {
        indexSource: "ManifestInterfaceData.csv",
        assetUrlTemplate: `${WAGO_CASC_URL}/{fdid}`,
        version,
      },
      summary: {
        candidateCount: candidates.length,
        exportedCount: entries.length,
        failureCount: failures.length,
      },
      entries,
      failures,
    },
    { spaces: 2 },
  );
  console.log(`Wrote loadscreens: ${outputDir}`);
  if (failures.length) {
    throw new Error(`Loadscreen export failed for ${failures.length} assets`);
  }
}
