"""Focused tests for durable Wago corpus discovery and semantic diffs."""

from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

import duckdb
from wago_research import Corpus, attribute_bits_sql, diff_corpora


def write_csv(path: Path, header: list[str], records: list[list[int]]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(records)


class WagoResearchTests(unittest.TestCase):
    def make_corpus(
        self,
        parent: Path,
        name: str,
        *,
        aura: int,
        attribute_word_zero: int,
        effect_attributes: int = 0,
    ) -> Corpus:
        root = parent / name
        root.mkdir()
        write_csv(
            root / "SpellEffect.csv",
            [
                "ID",
                "SpellID",
                "DifficultyID",
                "EffectIndex",
                "EffectAura",
                "EffectAttributes",
                "ImplicitTarget_0",
                "ImplicitTarget_1",
            ],
            [[10, 1, 0, 0, aura, effect_attributes, 1, 0]],
        )
        attributes = ["Attributes_0"] + [
            f"Attributes_{index}" for index in range(1, 17)
        ]
        write_csv(
            root / "SpellMisc.csv",
            ["ID", "SpellID", "DifficultyID", *attributes],
            [[20, 1, 0, attribute_word_zero, *([0] * 16)]],
        )
        return Corpus(root)

    def test_attribute_flattening_handles_signed_bit_31(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            corpus = self.make_corpus(
                Path(directory), "corpus", aura=3, attribute_word_zero=-2147483648
            )
            con = duckdb.connect(":memory:")
            result = con.execute(
                f"SELECT raw FROM ({attribute_bits_sql(corpus)})"
            ).fetchall()
            self.assertEqual(result, [(31,)])

    def test_diff_reports_added_aura_and_attribute(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            old = self.make_corpus(parent, "old", aura=3, attribute_word_zero=1)
            new = self.make_corpus(
                parent,
                "new",
                aura=4,
                attribute_word_zero=2,
                effect_attributes=4,
            )
            diff = diff_corpora(old, new)
            self.assertEqual(diff["surfaces"]["spell_effects"]["counts"]["changed"], 1)
            self.assertEqual(diff["surfaces"]["aura_raws"]["counts"]["added"], 1)
            self.assertEqual(
                diff["surfaces"]["spell_attribute_bits"]["counts"]["removed"], 1
            )
            self.assertEqual(
                diff["surfaces"]["spell_attribute_bits"]["counts"]["added"], 1
            )
            self.assertEqual(
                diff["surfaces"]["effect_attribute_bits"]["counts"]["added"], 1
            )


if __name__ == "__main__":
    unittest.main()
