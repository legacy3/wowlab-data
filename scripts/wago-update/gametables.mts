import pRetry from "p-retry";
import { fs, path } from "zx";

import type { CliOptions } from "./types.mts";

import { failed, failure, runPool } from "./pool.mts";
import { cascUrl, fetchText } from "./wago.mts";

export const GAME_TABLES: { fdid: number; name: string }[] = [
  { fdid: 1391642, name: "HpPerSta" },
  { fdid: 1391669, name: "CombatRatings" },
  { fdid: 1391670, name: "CombatRatingsMultByILvl" },
  { fdid: 1980632, name: "StaminaMultByILvl" },
  { fdid: 1391664, name: "BaseMp" },
  { fdid: 1391660, name: "SpellScaling" },
  { fdid: 1385707, name: "ArmorMitigationByLvl" },
  { fdid: 1391652, name: "NpcTotalHp" },
  { fdid: 1391644, name: "NpcDamageByClass" },
  { fdid: 1391668, name: "ChallengeModeHealth" },
  { fdid: 1391667, name: "ChallengeModeDamage" },
  { fdid: 1391643, name: "ItemSocketCostPerLevel" },
  { fdid: 4492239, name: "ProfessionRatings" },
  { fdid: 4494528, name: "BaseProfessionRatings" },
];

type DownloadOptions = Pick<CliOptions, "workers" | "timeout" | "attempts">;

/// Download every GameTable at `version` into `dest` as `<name>.txt`, skipping empty
/// (deprecated) tables. Throws if any download fails after retries.
export async function downloadGameTables(
  dest: string,
  version: string,
  args: DownloadOptions,
): Promise<void> {
  const results = await runPool(GAME_TABLES, args.workers, ({ fdid, name }) =>
    pRetry(
      async () => {
        const text = await fetchText(cascUrl(fdid, version), args.timeout);
        if (text.trim() === "") {
          // Deprecated/empty table for this build; drop any stale file.
          await fs.remove(path.join(dest, `${name}.txt`));
          return { bytes: 0, name, ok: true as const };
        }
        const finalText = text.endsWith("\n") ? text : `${text}\n`;
        await fs.writeFile(path.join(dest, `${name}.txt`), finalText, "utf8");
        return { bytes: Buffer.byteLength(finalText), name, ok: true as const };
      },
      { retries: args.attempts },
    ).catch((err) => failure(name, err)),
  );

  const failures = results.filter(failed);
  const written = results.filter((r) => r.ok && (r.bytes ?? 0) > 0).length;
  console.log(
    `GameTables: wrote ${written}, skipped ${
      GAME_TABLES.length - written - failures.length
    } empty, ${failures.length} failed`,
  );
  if (failures.length) {
    for (const f of failures.slice(0, 50)) {
      console.log(`  ${f.name}: ${f.error}`);
    }
    throw new Error(`Aborting: ${failures.length} GameTable downloads failed`);
  }
}
