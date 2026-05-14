import type { BuildInfo } from "./types.mts";

export const WAGO_BUILDS_URL = "https://wago.tools/api/builds";
export const WAGO_DB2_CSV_URL = "https://wago.tools/db2";
export const WAGO_CASC_URL = "https://wago.tools/api/casc";
const USER_AGENT = "wowlab-data-updater/2.0";

export function cascUrl(fdid: number, version: string): string {
  const url = new URL(`${WAGO_CASC_URL}/${fdid}`);
  url.searchParams.set("version", version);
  return url.toString();
}

export async function fetchBytes(
  url: string,
  timeoutSeconds: number,
): Promise<Buffer> {
  const res = await fetch(url, {
    headers: { "user-agent": USER_AGENT },
    signal: timeoutSignal(timeoutSeconds),
  });
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}`);
  }
  return Buffer.from(await res.arrayBuffer());
}

export async function fetchText(
  url: string,
  timeoutSeconds: number,
): Promise<string> {
  const res = await fetch(url, {
    headers: { "user-agent": USER_AGENT },
    signal: timeoutSignal(timeoutSeconds),
  });
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}`);
  }
  return await res.text();
}

export async function latestBuild(
  product: string,
  timeoutSeconds: number,
): Promise<BuildInfo> {
  const data = JSON.parse(
    await fetchText(WAGO_BUILDS_URL, timeoutSeconds),
  ) as Record<string, BuildInfo[]>;
  const builds = data[product];
  if (!builds?.length) {
    throw new Error(
      `No Wago builds for product ${product}. Available: ${Object.keys(data).sort().join(", ")}`,
    );
  }
  return builds[0];
}

export function tableUrl(table: string, version: string): string {
  const url = new URL(`${WAGO_DB2_CSV_URL}/${encodeURIComponent(table)}/csv`);
  url.searchParams.set("version", version);
  return url.toString();
}

function timeoutSignal(seconds: number): AbortSignal {
  return AbortSignal.timeout(Math.max(1, seconds) * 1000);
}
