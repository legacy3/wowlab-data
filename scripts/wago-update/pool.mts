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
): Promise<R[]> {
  let done = 0;
  const results = await pMap(
    items,
    async (item) => {
      const result = await task(item);
      done++;
      if (done % 50 === 0 || done === items.length) {
        console.log(`  progress: ${done}/${items.length}`);
      }
      return result;
    },
    { concurrency: Math.max(1, workers) },
  );

  return results.sort((a, b) => a.name.localeCompare(b.name));
}
