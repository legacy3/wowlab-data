import { path } from "zx";

import type {
  CliOptions,
  FailedExport,
  LoadscreenCandidate,
} from "./types.mts";

import { loadCsvObjects, parsePositiveInt } from "./csv.mts";
import { uploadPngAssetIfMissing, uploadSeoVariant } from "./journal.mts";
import { dataDir } from "./paths.mts";
import { failed, failure, ok, runPool } from "./pool.mts";
import {
  imageVariant,
  listExisting,
  storageContext,
  uploadJson,
} from "./storage.mts";
import { cascUrl, WAGO_CASC_URL } from "./wago.mts";

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
  const exported = await runPool(
    candidates,
    args.assetWorkers,
    async (candidate) => {
      try {
        const { asset: image, png } = await uploadPngAssetIfMissing(
          ctx,
          candidate.fileDataId,
          `loadscreens/images/${candidate.fileDataId}.png`,
          version,
          args,
        );
        const full = imageVariant(ctx, image);
        return {
          ...candidate,
          bytes: image.bytes,
          full,
          name: String(candidate.fileDataId),
          ok: true,
          seo: png
            ? await uploadSeoVariant(
                ctx,
                png,
                `loadscreens/seo/${candidate.fileDataId}.png`,
              )
            : full,
          sourceUrl: cascUrl(candidate.fileDataId, version),
        };
      } catch (err) {
        return {
          ...candidate,
          ...(failure(candidate.fileDataId, err) as FailedExport),
        };
      }
    },
    args.assetJobTimeout,
  );
  const entries = exported.filter(ok);
  const failures = exported.filter(failed);

  await uploadJson(ctx, "loadscreens/manifest.json", {
    entries,
    failures,
    generatedAt: new Date().toISOString(),
    source: {
      assetUrlTemplate: `${WAGO_CASC_URL}/{fdid}`,
      indexSource: "ManifestInterfaceData.csv",
      version,
    },
    summary: {
      candidateCount: candidates.length,
      exportedCount: entries.length,
      failureCount: failures.length,
      uploadedFullImages: entries.filter(
        (entry) => entry.full && entry.full.path,
      ).length,
    },
  });
  console.log(`Uploaded loadscreens: ${ctx.bucket}/loadscreens`);
  if (failures.length) {
    throw new Error(`Loadscreen export failed for ${failures.length} assets`);
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
    category: stem.toLowerCase().includes("dungeon")
      ? "dungeon"
      : stem.toLowerCase().includes("raid")
        ? "raid"
        : stem.toLowerCase().includes("zone")
          ? "zone"
          : "other",
    fileDataId,
    filename: fullPath,
    key: normalizeKey(stem),
    stem,
  };
}

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
