import { createRequire } from "node:module";
import pRetry from "p-retry";
import sharp from "sharp";

import type { CliOptions } from "./types.mts";

import { cascUrl, fetchBytes } from "./wago.mts";

const require = createRequire(import.meta.url);
const BLPFile = require("js-blp");
const SEO_RATIO = 2992 / 1148;

export type PngImage = {
  bytes: Buffer;
  width: number;
  height: number;
};

export async function centerCropPng(input: Buffer): Promise<PngImage> {
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
    .extract({ height: cropH, left, top, width: cropW })
    .png()
    .toBuffer({ resolveWithObject: true });
  return { bytes: data, height: info.height, width: info.width };
}

export async function fetchBlpAsPng(
  fdid: number,
  version: string,
  args: CliOptions,
): Promise<PngImage> {
  return await pRetry(
    async () =>
      await blpToPng(
        await fetchBytes(cascUrl(fdid, version), args.assetTimeout),
      ),
    {
      maxTimeout: 10_000,
      minTimeout: 2_000,
      retries: Math.max(1, args.attempts) - 1,
    },
  );
}

async function blpToPng(raw: Buffer): Promise<PngImage> {
  const blp = new BLPFile(raw);
  const pixels = blp.getPixels(0);
  const pixelBuffer = Buffer.from(pixels.raw ?? pixels._buffer ?? pixels);
  const { data, info } = await sharp(pixelBuffer, {
    raw: { channels: 4, height: blp.height, width: blp.width },
  })
    .png()
    .toBuffer({ resolveWithObject: true });
  return { bytes: data, height: info.height, width: info.width };
}
