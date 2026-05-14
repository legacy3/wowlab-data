import { path } from "zx";

import type {
  CliOptions,
  FailedExport,
  ImageAsset,
  JournalAssetExport,
  JournalAssetReference,
  JournalInstance,
  JournalSlot,
} from "./types.mts";

import { loadCsvObjects, parsePositiveInt } from "./csv.mts";
import { centerCropPng, fetchBlpAsPng, type PngImage } from "./images.mts";
import { dataDir } from "./paths.mts";
import { failed, failure, ok, runPool } from "./pool.mts";
import {
  imageVariant,
  listExisting,
  objectExists,
  storageContext,
  type StorageContext,
  uploadJson,
  uploadObject,
} from "./storage.mts";
import { cascUrl, WAGO_CASC_URL } from "./wago.mts";

export async function exportJournalImages(version: string, args: CliOptions) {
  const ctx = storageContext(args);
  const imagesPrefix = "journal/images";
  const encountersPrefix = "journal/encounters";
  const manifest = await loadInterfaceManifest();
  const { instances, references } = collectJournalAssets(
    await loadCsvObjects(path.join(dataDir, "JournalInstance.csv")),
  );
  let fdids = [...references.keys()].sort((a, b) => a - b);
  if (args.assetLimit !== undefined) {
    fdids = fdids.slice(0, Math.max(0, args.assetLimit));
  }

  await Promise.all([
    listExisting(ctx, imagesPrefix),
    listExisting(ctx, encountersPrefix),
  ]);
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
          ...meta,
          bytes: image.bytes,
          name: String(fdid),
          ok: true,
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
    JournalInstance &
      Record<JournalSlot, ReturnType<typeof imageVariant> | null>
  > = {};

  for (const instance of instances) {
    const out: JournalInstance &
      Record<JournalSlot, ReturnType<typeof imageVariant> | null> = {
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
    coveragePercent:
      Math.round((assets.length / Math.max(1, fdids.length)) * 10000) / 100,
    existingImages: assets.filter((asset) => asset.status === "exists").length,
    exportedImages: assets.length,
    failedImages: failures.length,
    journalInstances: instances.length,
    requestedImages: fdids.length,
    uploadedImages: assets.filter((asset) => asset.status === "uploaded")
      .length,
  };

  const encounterResults = await exportEncounterImages(
    ctx,
    encountersPrefix,
    version,
    args,
  );
  await uploadJson(ctx, "journal/manifest.json", {
    assets,
    encounterAssets: encounterResults.assets,
    failures,
    encounterFailures: encounterResults.failures,
    generatedAt,
    imagesPrefix,
    encountersPrefix,
    source: { assetUrlTemplate: `${WAGO_CASC_URL}/{fdid}`, version },
    summary,
  });
  await uploadJson(ctx, "journal/journal-instance-images.json", {
    generatedAt,
    instancesById,
    summary,
  });
  console.log(`Uploaded journal images: ${ctx.bucket}/${imagesPrefix}`);
  if (failures.length || encounterResults.failures.length) {
    throw new Error(
      `Journal image export failed for ${failures.length + encounterResults.failures.length} assets`,
    );
  }
}

async function exportEncounterImages(
  ctx: StorageContext,
  encountersPrefix: string,
  version: string,
  args: CliOptions,
) {
  let fdids = [
    ...new Set(
      (await loadCsvObjects(path.join(dataDir, "JournalEncounterCreature.csv")))
        .map((row) => parsePositiveInt(row.FileDataID))
        .filter((fdid): fdid is number => typeof fdid === "number"),
    ),
  ].sort((a, b) => a - b);
  if (args.assetLimit !== undefined) {
    fdids = fdids.slice(0, Math.max(0, args.assetLimit));
  }

  console.log(`Unique encounter image IDs: ${fdids.length}`);
  const results = await runPool<number, JournalAssetExport | FailedExport>(
    fdids,
    args.assetWorkers,
    async (fdid) => {
      try {
        const { asset: image } = await uploadPngAsset(
          ctx,
          fdid,
          `${encountersPrefix}/${fdid}.png`,
          version,
          args,
        );
        return {
          ...image,
          bytes: image.bytes,
          name: String(fdid),
          ok: true,
          references: [],
        };
      } catch (err) {
        return failure(fdid, err) as FailedExport;
      }
    },
  );

  return {
    assets: results.filter(ok),
    failures: results.filter(failed),
  };
}

export async function uploadSeoVariant(
  ctx: StorageContext,
  full: PngImage,
  storagePath: string,
) {
  const seo = await centerCropPng(full.bytes);
  const exists = await objectExists(ctx, storagePath);
  if (!exists) {
    await uploadObject(ctx, storagePath, seo.bytes, "image/png", false);
  }
  return {
    bytes: seo.bytes.length,
    height: seo.height,
    path: storagePath,
    publicUrl: imageVariant(ctx, {
      bytes: seo.bytes.length,
      fileDataId: 0,
      height: seo.height,
      sourceUrl: "",
      status: exists ? "exists" : "uploaded",
      storagePath,
      width: seo.width,
    }).publicUrl,
    width: seo.width,
  };
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
      backgroundFileDataId: parsePositiveInt(row.BackgroundFileDataID),
      buttonFileDataId: parsePositiveInt(row.ButtonFileDataID),
      journalInstanceId,
      loreFileDataId: parsePositiveInt(row.LoreFileDataID),
      mapId: parsePositiveInt(row.MapID),
      name: row.Name_lang ?? "",
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

async function loadInterfaceManifest() {
  const manifest = new Map<number, { fileName: string; filePath: string }>();
  for (const row of await loadCsvObjects(
    path.join(dataDir, "ManifestInterfaceData.csv"),
  )) {
    const id = parsePositiveInt(row.ID);
    if (id) {
      manifest.set(id, {
        fileName: row.FileName ?? "",
        filePath: row.FilePath ?? "",
      });
    }
  }
  return manifest;
}

export { uploadPngAsset };

async function uploadPngAsset(
  ctx: StorageContext,
  fdid: number,
  storagePath: string,
  version: string,
  args: CliOptions,
): Promise<{ asset: ImageAsset; png: PngImage }> {
  const png = await fetchBlpAsPng(fdid, version, args);
  const exists = await objectExists(ctx, storagePath);
  if (!exists) {
    await uploadObject(ctx, storagePath, png.bytes, "image/png", false);
  }
  return {
    asset: {
      bytes: png.bytes.length,
      fileDataId: fdid,
      height: png.height,
      sourceUrl: cascUrl(fdid, version),
      status: exists ? "exists" : "uploaded",
      storagePath,
      width: png.width,
    },
    png,
  };
}
