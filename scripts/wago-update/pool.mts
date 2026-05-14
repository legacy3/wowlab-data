import pMap from "p-map";
import type { JobResult } from "./types.mts";

export function ok<T extends JobResult>(result: T): result is T & { ok: true } {
  return result.ok;
}

export function failed<T extends JobResult>(
  result: T,
): result is T & { ok: false } {
  return !result.ok;
}

export function failure(
  name: string | number,
  err: unknown,
): JobResult & { ok: false } {
  return {
    name: String(name),
    ok: false,
    error: err instanceof Error ? err.message : String(err),
  };
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
