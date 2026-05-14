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
  const results: R[] = [];
  let index = 0;
  let done = 0;
  const count = Math.max(1, workers);

  await Promise.all(
    Array.from({ length: count }, async () => {
      while (index < items.length) {
        const item = items[index++];
        results.push(await task(item));
        done++;
        if (done % 50 === 0 || done === items.length) {
          console.log(`  progress: ${done}/${items.length}`);
        }
      }
    }),
  );

  return results.sort((a, b) => a.name.localeCompare(b.name));
}
