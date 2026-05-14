import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import pRetry from "p-retry";

import type { CliOptions, ImageAsset, ImageVariant } from "./types.mts";

const DEFAULT_CACHE_CONTROL = "3600";
const SUPABASE_TIMEOUT_MS = 30_000;

export type StorageContext = {
  supabase: SupabaseClient;
  baseUrl: string;
  bucket: string;
  existing: Map<string, Set<string>>;
};

export class FatalStorageError extends Error {}

export function imageVariant(
  ctx: StorageContext,
  image: ImageAsset,
): ImageVariant {
  return {
    bytes: image.bytes,
    height: image.height,
    path: image.storagePath,
    publicUrl: publicUrl(ctx, image.storagePath),
    width: image.width,
  };
}

export async function listExisting(
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
    const data = await pRetry(
      async () => {
        const { data, error } = await ctx.supabase.storage
          .from(ctx.bucket)
          .list(dir, {
            limit: 1000,
            offset,
            sortBy: { column: "name", order: "asc" },
          });
        if (error) {
          throw error;
        }
        return data ?? [];
      },
      { maxTimeout: 5_000, minTimeout: 1_000, retries: 2 },
    ).catch((err: unknown) => {
      throw storageError(`list ${ctx.bucket}/${dir}`, err);
    });

    for (const item of data) {
      files.add(item.name);
    }
    if (data.length < 1000) {
      break;
    }
    offset += data.length;
  }

  ctx.existing.set(dir, files);
  return files;
}

export async function objectExists(ctx: StorageContext, storagePath: string) {
  const { dir, file } = splitStoragePath(storagePath);
  return (await listExisting(ctx, dir)).has(file);
}

export function publicUrl(ctx: StorageContext, storagePath: string) {
  return ctx.supabase.storage.from(ctx.bucket).getPublicUrl(storagePath).data
    .publicUrl;
}

export function storageContext(args: CliOptions): StorageContext {
  const { baseUrl, supabase } = storageClient();
  return {
    baseUrl,
    bucket: args.supabaseBucket,
    existing: new Map(),
    supabase,
  };
}

export async function uploadJson(
  ctx: StorageContext,
  storagePath: string,
  body: unknown,
) {
  await uploadObject(
    ctx,
    storagePath,
    `${JSON.stringify(body, null, 2)}\n`,
    "application/json",
    true,
  );
}

export async function uploadObject(
  ctx: StorageContext,
  storagePath: string,
  body: Buffer | string,
  contentType: string,
  upsert: boolean,
) {
  const bytes = typeof body === "string" ? Buffer.from(body) : body;
  await pRetry(
    async () => {
      const { error } = await ctx.supabase.storage
        .from(ctx.bucket)
        .upload(storagePath, bytes, {
          cacheControl: DEFAULT_CACHE_CONTROL,
          contentType,
          upsert,
        });
      if (error) {
        throw error;
      }
    },
    { maxTimeout: 5_000, minTimeout: 1_000, retries: 2 },
  ).catch((err: unknown) => {
    throw storageError(`upload ${ctx.bucket}/${storagePath}`, err);
  });

  const { dir, file } = splitStoragePath(storagePath);
  ctx.existing.get(dir)?.add(file);
}

export async function verifyStorageAccess(args: CliOptions) {
  const ctx = storageContext(args);
  console.log(`Supabase Storage endpoint: ${storageEndpoint(ctx)}`);
  console.log(`Supabase Storage bucket: ${ctx.bucket}`);
  await pRetry(
    async () => {
      const { data, error } = await ctx.supabase.storage.getBucket(ctx.bucket);
      if (error) {
        throw error;
      }
      if (!data) {
        throw new Error(`bucket ${ctx.bucket} was not returned`);
      }
    },
    { maxTimeout: 5_000, minTimeout: 1_000, retries: 2 },
  ).catch((err: unknown) => {
    throw storageError(`access bucket ${ctx.bucket}`, err);
  });
}

function errorCause(err: unknown): string | null {
  if (!(err instanceof Error)) {
    return null;
  }

  const parts: string[] = [];
  let cause: unknown = err.cause;
  while (cause instanceof Error) {
    parts.push(`${cause.name}: ${cause.message}`);
    cause = cause.cause;
  }
  const original = (err as { originalError?: unknown }).originalError;
  if (original instanceof Error) {
    parts.push(`${original.name}: ${original.message}`);
  }
  return parts.length ? parts.join("; ") : null;
}

function requestUrl(input: RequestInfo | URL) {
  const raw =
    typeof input === "string"
      ? input
      : input instanceof URL
        ? input.toString()
        : input.url;
  const parsed = new URL(raw);
  return `${parsed.origin}${parsed.pathname}`;
}

function splitStoragePath(storagePath: string) {
  const slash = storagePath.lastIndexOf("/");
  return {
    dir: slash === -1 ? "" : storagePath.slice(0, slash),
    file: slash === -1 ? storagePath : storagePath.slice(slash + 1),
  };
}

function statusCode(err: unknown) {
  const raw = (err as { statusCode?: number | string }).statusCode;
  return typeof raw === "string" ? Number.parseInt(raw, 10) : raw;
}

function storageClient(): { baseUrl: string; supabase: SupabaseClient } {
  const rawUrl = process.env.SUPABASE_URL?.trim();
  const key =
    process.env.SUPABASE_SERVICE_ROLE_KEY?.trim() ??
    process.env.SUPABASE_SERVICE_KEY?.trim();
  if (!rawUrl || !key) {
    throw new Error(
      "Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY to upload assets",
    );
  }

  const baseUrl = (
    rawUrl.startsWith("http") ? rawUrl : `https://${rawUrl}`
  ).replace(/\/+$/, "");
  return {
    baseUrl,
    supabase: createClient(baseUrl, key, {
      auth: { persistSession: false },
      global: { fetch: supabaseFetch },
    }),
  };
}

function storageEndpoint(ctx: StorageContext) {
  return new URL("/storage/v1", ctx.baseUrl).toString();
}

function storageError(action: string, err: unknown): Error {
  const message = err instanceof Error ? err.message : String(err);
  const cause = errorCause(err);
  const code = statusCode(err);
  if (code === 401 || code === 403) {
    return new FatalStorageError(
      `${action}: auth rejected: ${message}${cause ? `; cause: ${cause}` : ""}`,
    );
  }
  return new Error(`${action}: ${message}${cause ? `; cause: ${cause}` : ""}`);
}

async function supabaseFetch(input: RequestInfo | URL, init?: RequestInit) {
  const method =
    init?.method ?? (input instanceof Request ? input.method : "GET");
  const url = requestUrl(input);
  return await fetch(input, {
    ...init,
    signal: init?.signal ?? AbortSignal.timeout(SUPABASE_TIMEOUT_MS),
  }).catch((err: unknown) => {
    const message = err instanceof Error ? err.message : String(err);
    const cause = errorCause(err);
    throw new Error(
      `Supabase request failed: ${method} ${url}: ${message}${cause ? `; cause: ${cause}` : ""}`,
      { cause: err },
    );
  });
}
