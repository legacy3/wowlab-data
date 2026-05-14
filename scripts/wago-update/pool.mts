import pMap from "p-map";

import type { JobResult } from "./types.mts";

export function failed<T extends JobResult>(
  result: T,
): result is { ok: false } & T {
  return !result.ok;
}

export function failure(
  name: string | number,
  err: unknown,
): { ok: false } & JobResult {
  return {
    error: err instanceof Error ? err.message : String(err),
    name: String(name),
    ok: false,
  };
}

export function ok<T extends JobResult>(result: T): result is { ok: true } & T {
  return result.ok;
}

export async function runPool<T, R extends JobResult>(
  items: T[],
  workers: number,
  task: (item: T) => Promise<R>,
  timeoutSeconds?: number,
): Promise<R[]> {
  let done = 0;
  const results = await pMap(
    items,
    async (item) => {
      const startedAt = performance.now();
      const result = await runJob(task, item, timeoutSeconds);
      done++;
      const durationMs = Math.round(performance.now() - startedAt);
      if (done % 50 === 0 || done === items.length) {
        console.log(`  progress: ${done}/${items.length}`);
      }
      if (durationMs >= 10_000) {
        console.log(`  slow asset ${result.name}: ${durationMs}ms`);
      }
      return result;
    },
    { concurrency: Math.max(1, workers) },
  );

  return results.sort((a, b) => a.name.localeCompare(b.name));
}

async function runJob<T, R extends JobResult>(
  task: (item: T) => Promise<R>,
  item: T,
  timeoutSeconds?: number,
): Promise<R> {
  if (!timeoutSeconds) {
    return await task(item);
  }

  let timeout: NodeJS.Timeout | undefined;
  try {
    return await Promise.race([
      task(item),
      new Promise<R>((_, reject) => {
        timeout = setTimeout(() => {
          reject(new Error(`asset job timed out after ${timeoutSeconds}s`));
        }, timeoutSeconds * 1000);
      }),
    ]);
  } catch (err) {
    return failure(jobName(item), err) as R;
  } finally {
    clearTimeout(timeout);
  }
}

function jobName(item: unknown): string | number {
  if (typeof item === "number" || typeof item === "string") {
    return item;
  }
  if (item && typeof item === "object") {
    const candidate = item as { fileDataId?: unknown; name?: unknown };
    if (
      typeof candidate.fileDataId === "number" ||
      typeof candidate.fileDataId === "string"
    ) {
      return candidate.fileDataId;
    }
    if (
      typeof candidate.name === "number" ||
      typeof candidate.name === "string"
    ) {
      return candidate.name;
    }
  }
  return "unknown";
}
