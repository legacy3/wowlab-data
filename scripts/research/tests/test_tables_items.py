"""Table loading, indexing and ItemTemplate assembly."""

from __future__ import annotations

import pytest
from synthetic import SnapshotBuilder, item_row, minimal_items, sparse_row, write_csv

from gearing import SourceError
from gearing.items import ItemStore
from gearing.tables import GameTable, Table, Tables, coerce


@pytest.mark.parametrize("text, expected", [
    ("", 0), ("0", 0), ("42", 42), ("-1", -1), ("1.5", 1.5),
    ("1e3", 1000.0), ("hello", "hello"), ("2147483647", 2147483647),
])
def test_coerce(text, expected):
    assert coerce(text) == expected


def test_table_indexes(tmp_path):
    write_csv(tmp_path / "T.csv", ["ID", "Parent", "Value"],
              [[1, 10, "a"], [2, 10, "b"], [3, 11, "c"]])
    table = Table("T", tmp_path / "T.csv")
    assert len(table) == 3
    assert table.by("ID")[2]["Value"] == "b"
    assert [r["ID"] for r in table.group("Parent")[10]] == [1, 2]
    assert table.lookup(99) is None
    with pytest.raises(SourceError, match="no row with"):
        table.require(99)
    with pytest.raises(SourceError, match="no column"):
        table.by("Nope")


def test_table_unique_index_keeps_the_last_row(tmp_path):
    write_csv(tmp_path / "T.csv", ["ID", "Value"], [[1, "first"], [1, "second"]])
    table = Table("T", tmp_path / "T.csv")
    assert table.by("ID")[1]["Value"] == "second"


def test_group_preserves_csv_row_order(tmp_path):
    write_csv(tmp_path / "T.csv", ["ID", "Parent"],
              [[5, 1], [3, 1], [9, 1]])
    table = Table("T", tmp_path / "T.csv")
    assert [r["ID"] for r in table.group("Parent")[1]] == [5, 3, 9]


def test_empty_table_fails_closed(tmp_path):
    (tmp_path / "T.csv").write_text("", encoding="utf-8")
    with pytest.raises(SourceError, match="is empty"):
        Table("T", tmp_path / "T.csv")


def test_gametable_row_and_column(tmp_path):
    (tmp_path / "G.txt").write_text("Level\tA\tB\n1\t1.5\t2.5\n2\t3.5\t4.5\n",
                                    encoding="utf-8")
    gt = GameTable("G", tmp_path / "G.txt")
    assert gt.columns == ["A", "B"]
    assert gt.row(2) == [3.5, 4.5]
    assert gt.column(1, 1) == 2.5
    assert gt.row(99) is None
    assert gt.column(99, 0) is None
    assert gt.column_index("B") == 1
    with pytest.raises(SourceError, match="no column"):
        gt.column_index("Z")
    with pytest.raises(SourceError, match="asked for column"):
        gt.column(1, 9)
    # Trinity's GetTableRowCount includes the implicit zero row.
    assert gt.row_count() == 3


def test_tables_missing_file_fails_closed(tmp_path):
    tables = Tables(tmp_path)
    with pytest.raises(SourceError, match="missing source table"):
        tables("Nope")
    with pytest.raises(SourceError, match="missing game table"):
        tables.gametable("Nope")
    assert tables.optional("Nope") is None


def test_tables_missing_directory_fails_closed(tmp_path):
    with pytest.raises(SourceError, match="table directory not found"):
        Tables(tmp_path / "absent")


def test_table_hashes_are_stable(tmp_path):
    write_csv(tmp_path / "T.csv", ["ID"], [[1]])
    tables = Tables(tmp_path)
    first = tables.hashes(["T"])
    assert set(first) == {"T.csv"}
    assert tables.hashes(["T"]) == first
    with pytest.raises(SourceError, match="cannot hash"):
        tables.hashes(["Nope"])


# -- ItemTemplate ----------------------------------------------------------

def test_item_template_accessors(tmp_path):
    builder = SnapshotBuilder(tmp_path)
    minimal_items(
        builder, [item_row(ID=1, ClassID=2, SubclassID=8)],
        [sparse_row(ID=1, name="Blade", ItemLevel=200, RequiredLevel=80,
                    InventoryType=17, OverallQualityID=4, ItemDelay=3600,
                    DmgVariance=0.5, ItemSet=42, Gem_properties=7,
                    Socket_match_enchantment_ID=9, ExpansionID=11,
                    sockets=[7, 0, 0], stats=[(4, 1000)],
                    flags=[0, 0x200, 0, 0, 0])])
    proto = ItemStore(builder.build()).get(1)
    assert (proto.class_id, proto.subclass_id) == (2, 8)
    assert proto.name == "Blade"
    assert (proto.base_item_level, proto.base_required_level) == (200, 80)
    assert (proto.delay, proto.dmg_variance) == (3600, 0.5)
    assert (proto.item_set, proto.gem_properties, proto.socket_bonus) == (42, 7, 9)
    assert proto.is_weapon and not proto.is_armor
    assert proto.is_caster_weapon
    assert proto.is_equippable
    assert proto.has_any_socket and proto.has_any_stat
    assert proto.socket_color(0) == 7
    assert proto.stat_type(0) == 4 and proto.stat_percent_editor(0) == 1000
    assert proto.stat_type(1) == -1


def test_missing_item_rows_fail_closed(tmp_path):
    builder = SnapshotBuilder(tmp_path)
    minimal_items(builder, [item_row(ID=1)], [sparse_row(ID=2)])
    store = ItemStore(builder.build())
    with pytest.raises(SourceError, match="no ItemSparse row"):
        store.get(1)
    with pytest.raises(SourceError, match="no Item row"):
        store.get(2)
    with pytest.raises(SourceError, match="absent from both"):
        store.get(3)
    assert store.exists(1) is False


def test_item_effect_missing_row_is_skipped(tmp_path):
    builder = SnapshotBuilder(tmp_path)
    minimal_items(
        builder, [item_row(ID=1)], [sparse_row(ID=1)],
        effects=[],
        x_effects=[{"ID": 1, "ItemEffectID": 77, "ItemID": 1}])
    assert ItemStore(builder.build()).get(1).effects == []


@pytest.mark.snapshot
def test_real_snapshot_layout_matches_the_port(tables):
    """Column names the port depends on must exist in this snapshot."""
    sparse = tables("ItemSparse")
    for column in ("ItemLevel", "OverallQualityID", "InventoryType",
                   "PlayerLevelToItemLevelCurveID", "ItemLevelOffsetCurveID",
                   "ItemLevelOffsetItemLevel", "ItemSquishEraID",
                   "Gem_properties", "Socket_match_enchantment_ID",
                   "StatModifier_bonusStat_0", "StatPercentEditor_0",
                   "StatPercentageOfSocket_0", "SocketType_0", "Flags_0"):
        assert column in sparse.columns, column
    for name, columns in {
        "ItemBonus": ("Value_0", "ParentItemBonusListID", "Type", "OrderIndex"),
        "ItemBonusTreeNode": ("ItemContext", "ChildItemBonusTreeID",
                              "ChildItemLevelSelectorID", "MinMythicPlusLevel"),
        "CurvePoint": ("Pos_0", "Pos_1", "CurveID", "OrderIndex"),
        "ContentTuning": ("MinLevelSquish", "MaxLevelSquish",
                          "MinLevelScalingOffset", "MaxLevelScalingOffset"),
        "ItemScalingConfig": ("ItemOffsetCurveID", "ItemLevel", "RequiredLevel",
                              "ItemSquishEraID", "Flags"),
        "AzeriteUnlockMapping": ("MinItemLevel", "HeadBonus", "SetID"),
        "SpellItemEnchantment": ("ItemLevelMin", "ItemLevelMax", "Condition_ID"),
    }.items():
        for column in columns:
            assert column in tables(name).columns, f"{name}.{column}"


@pytest.mark.snapshot
def test_scaling_tables_are_keyed_by_item_level(tables):
    """ID == ItemLevel for every table Trinity looks up by item level."""
    for name in ("ItemArmorTotal", "ItemArmorShield", "ItemDamageOneHand",
                 "ItemDamageOneHandCaster", "ItemDamageTwoHand",
                 "ItemDamageTwoHandCaster", "ItemDamageAmmo"):
        table = tables(name)
        assert table.rows, name
        assert all(int(r["ID"]) == int(r["ItemLevel"]) for r in table), name
