#!/usr/bin/env python3
"""Derive expected witness values from raw source rows.

This script deliberately does **not** import the gearing package.  It reads the
CSV/GameTable rows directly and redoes the TrinityCore expressions by hand, so
``witnesses.json`` is an independent expectation rather than a recording of the
implementation under test.

    python3 tests/fixtures/derive_witnesses.py tests/fixtures/witness_inputs.json

The output is merged into ``witnesses.json`` together with the hand-written
curve/set/gem/enchant/upgrade sections.  Regenerate it only when the data
snapshot changes, and re-read the diff: a change here is a change in the source
data, not in the port.
"""
import csv, json, math, sys
from pathlib import Path

T = Path(__file__).resolve().parents[4] / 'data' / 'tables'

def load(name, key='ID'):
    out = {}
    with (T / f'{name}.csv').open(newline='') as f:
        for row in csv.DictReader(f):
            out.setdefault(row[key], row)
    return out

def loadall(name):
    with (T / f'{name}.csv').open(newline='') as f:
        return list(csv.DictReader(f))

def gt(name):
    lines = (T / f'{name}.txt').read_text().splitlines()
    cols = lines[0].split('\t')[1:]
    rows = {}
    for line in lines[1:]:
        if not line.strip(): continue
        p = line.split('\t')
        rows[int(p[0])] = [float(x) for x in p[1:]]
    return cols, rows

sparse = load('ItemSparse')
item = load('Item')
randprop = load('RandPropPoints')
_, cr_mult = gt('CombatRatingsMultByILvl')
_, sta_mult = gt('StaminaMultByILvl')
_, socket_cost = gt('ItemSocketCostPerLevel')
armor_q = load('ItemArmorQuality')
armor_t = load('ItemArmorTotal')
armor_s = load('ItemArmorShield')
armor_loc = load('ArmorLocation')

RP_IDX = {1:0,4:0,5:0,7:0,15:0,17:0,20:0,25:0,3:1,6:1,8:1,10:1,12:1,
          2:2,9:2,11:2,14:2,16:2,23:2,13:3,21:3,22:3,28:4}
RP_COL = {2:'GoodF',3:'SuperiorF',7:'SuperiorF',4:'EpicF',5:'EpicF',6:'EpicF'}
MULT_COL = {2:3,11:3,12:2,13:1,14:1,15:1,17:1,21:1,22:1,23:1,26:1}
RATING_MODS = {12,13,14,15,16,17,18,19,20,21,31,32,33,34,35,36,37,40,49,61,62,63,64}

def cround(v):
    return int(math.floor(v+0.5)) if v>=0 else -int(math.floor(-v+0.5))

def expect(item_id, ilvl, quality, extra_stats=None):
    s = sparse[str(item_id)]
    i = item[str(item_id)]
    inv = int(s['InventoryType']); sub = int(i['SubclassID']); cls = int(i['ClassID'])
    idx = RP_IDX.get(inv)
    col = RP_COL.get(quality)
    rpp = float(randprop[str(ilvl)][f'{col}_{idx}']) if (idx is not None and col) else 0.0
    mcol = MULT_COL.get(inv, 0)
    rmult = cr_mult[ilvl][mcol]; smult = sta_mult[ilvl][mcol]
    sc = socket_cost[ilvl][0]
    stats = []
    for n in range(10):
        st = int(s[f'StatModifier_bonusStat_{n}'])
        if st == -1: continue
        alloc = int(s[f'StatPercentEditor_{n}'])
        if extra_stats: alloc += extra_stats.get(n, 0)
        pct = float(s[f'StatPercentageOfSocket_{n}'])
        raw = (alloc*rpp)*0.0001 - pct*sc
        if st == 7: raw *= smult
        elif st in RATING_MODS: raw *= rmult
        stats.append({'index': n, 'stat_type': st, 'value': cround(raw)})
    armor = 0
    if cls == 4 and sub != 6 and 1 <= sub <= 4:
        q = 3 if quality == 7 else quality
        inv2 = 5 if inv == 20 else inv
        tot = {1:'Cloth',2:'Leather',3:'Mail',4:'Plate'}[sub]
        mod = {1:'Clothmodifier',2:'Leathermodifier',3:'Chainmodifier',4:'Platemodifier'}[sub]
        armor = int(float(armor_q[str(ilvl)][f'Qualitymod_{q}'])
                    * float(armor_t[str(ilvl)][tot])
                    * float(armor_loc[str(inv2)][mod]) + 0.5)
    elif cls == 4 and sub == 6:
        q = 3 if quality == 7 else quality
        armor = int(float(armor_s[str(ilvl)][f'Quality_{q}']) + 0.5)
    weapon = None
    if cls == 2:
        caster = bool(int(s['Flags_1']) & 0x200)
        table = None
        if inv == 17: table = 'ItemDamageTwoHandCaster' if caster else 'ItemDamageTwoHand'
        elif inv in (13,21,22): table = 'ItemDamageOneHandCaster' if caster else 'ItemDamageOneHand'
        elif inv in (15,25,26):
            if sub == 19: table = 'ItemDamageOneHandCaster'
            elif sub in (2,3,18): table = 'ItemDamageTwoHandCaster' if caster else 'ItemDamageTwoHand'
        elif inv == 24: table = 'ItemDamageAmmo'
        if table:
            q = 3 if quality == 7 else quality
            dps = float(load(table)[str(ilvl)][f'Quality_{q}'])
            delay = int(s['ItemDelay']); var = float(s['DmgVariance'])
            avg = dps*delay*0.001
            weapon = {'dps': dps,
                      'min_damage': (var*-0.5+1.0)*avg,
                      'max_damage': math.floor(avg*(var*0.5+1.0)+0.5),
                      'weapon_attack_power': int(dps*6.0)}
    return {'item_id': item_id, 'name': s['Display_lang'],
            'inventory_type': inv, 'class_id': cls, 'subclass_id': sub,
            'effective_item_level': ilvl, 'quality': quality,
            'rand_prop_points': rpp, 'rand_prop_index': idx,
            'rand_prop_column': col,
            'combat_ratings_mult_by_ilvl': rmult,
            'stamina_mult_by_ilvl': smult,
            'socket_cost_per_level': sc,
            'stats': stats, 'armor': armor, 'weapon': weapon}

spec = json.loads(Path(sys.argv[1]).read_text())
out = []
for w in spec:
    e = expect(w['item_id'], w['item_level'], w['quality'], w.get('extra_stats'))
    e.update({k: v for k, v in w.items() if k not in ('item_level','quality','extra_stats')})
    out.append(e)
print(json.dumps(out, indent=2))
