import { parse } from "csv-parse/sync";
import { fs } from "zx";

export function parseCsvRows(text: string): string[][] {
  return parse(text, {
    bom: true,
    relaxColumnCount: true,
    skipEmptyLines: false,
  }) as string[][];
}

export async function loadCsvObjects(
  file: string,
): Promise<Record<string, string>[]> {
  return parse(await fs.readFile(file, "utf8"), {
    bom: true,
    columns: true,
    relaxColumnCount: true,
    skipEmptyLines: true,
  }) as Record<string, string>[];
}

export function parsePositiveInt(value: unknown): number | undefined {
  const n = Number.parseInt(String(value ?? "").trim(), 10);
  return Number.isFinite(n) && n > 0 ? n : undefined;
}
