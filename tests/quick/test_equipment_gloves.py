import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / 'Windows') not in sys.path:
    sys.path.insert(0, str(ROOT / 'Windows'))

import native_equipment as eq
from native_cpk import unwrap, wrap


EMPTY_WESYS = struct.pack('<BBB5sII', 255, 0x21, 0x83, b'WESYS', 0, 0)


def _wesys(raw: bytes) -> bytes:
    return wrap(raw, EMPTY_WESYS)


def _glove_row(gid: int, name: str) -> bytes:
    row = bytearray(204)
    struct.pack_into('<H', row, 0, gid)
    encoded = name.encode('utf8')[:99]
    row[104:104 + len(encoded)] = encoded
    return bytes(row)


def _boot_row(bid: int, name: str) -> bytes:
    row = bytearray(304)
    struct.pack_into('<H', row, 0, bid)
    encoded = name.encode('utf8')[:99]
    row[204:204 + len(encoded)] = encoded
    return bytes(row)


class FakeCpk:
    def __init__(self, files: dict[str, bytes]):
        self.files = dict(files)

    def read(self, name: str) -> bytes:
        return self.files[name]


class GoalkeeperGlovesTests(unittest.TestCase):
    def test_validate_gloves_id(self):
        eq.validate({'boots_id': None, 'gloves_id': None, 'sleeves': 'keep', 'socks': 'keep'})
        eq.validate({'boots_id': 1, 'gloves_id': 101, 'sleeves': 'short', 'socks': 'long'})
        for bad in (-1, 65536, '101', True):
            with self.assertRaises(ValueError):
                eq.validate({'gloves_id': bad})

    def test_catalogue_and_legacy_fallback(self):
        boots_raw = _boot_row(1, 'Classic Boot')
        gloves_raw = b''.join([
            _glove_row(1, 'explorer grip'),
            _glove_row(101, 'Predator'),
            _glove_row(401, 'GK 200 GLOVE'),  # unverified texture -> excluded from picker
        ])
        cpk = FakeCpk({eq.BOOTS: _wesys(boots_raw), eq.GLOVES: _wesys(gloves_raw)})
        cat = eq.catalogue(cpk)
        self.assertEqual([g['id'] for g in cat['gloves']], [1, 101])

        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp) / 'native-equipment.json'
            legacy.write_text(json.dumps({'boots': [], 'sleeves': [], 'socks': []}), encoding='utf8')
            loaded = eq.read_catalogue(tmp)
            self.assertEqual(loaded, {'boots': [], 'sleeves': [], 'socks': []})
            self.assertEqual(len(eq.DEFAULT_GLOVES), 8)

    def test_compile_gloves_gk_only_and_preserve_originals(self):
        gloves_bin = b''.join([
            _glove_row(1, 'explorer grip'),
            _glove_row(101, 'Predator'),
            _glove_row(201, 'Vapor Grip3'),
        ])
        # Original rows: GK donor 50001 -> unmapped glove 111, another GK 50002 -> 201
        orig_rows = [(50001, 111), (50002, 201)]
        glove_list_raw = b''.join(struct.pack('<QQ', pid, gid) for pid, gid in orig_rows)
        glove_list_wrapped = _wesys(glove_list_raw)
        cpk = FakeCpk({eq.GLOVES: _wesys(gloves_bin), eq.GLOVE_ASSIGNMENTS: glove_list_wrapped})

        # 1. Non-GK player with stale gloves_id=101 must NOT modify GloveList.bin
        cf_proj = {
            'identity': {'name': 'Striker', 'position': 'CF'},
            'equipment': {'gloves_id': 101},
            'native_card': {'source_id': '50001'},
        }
        cf_def = {'identity': {'package_id': 'p-cf', 'card_id': 900001, 'person_id': 900002}}
        cf_bind = {'asset_key': '900003'}
        out_cf, rep_cf = eq.compile_gloves(cpk, [cf_proj], [cf_def], [cf_bind])
        self.assertEqual(out_cf, glove_list_wrapped)
        self.assertEqual(rep_cf[0]['mode'], 'non-gk-skipped')

        # 2. GK player with explicit gloves_id=101 + GK player inheriting unmapped 111 from donor 50001
        gk_sel = {
            'identity': {'name': 'Keeper A', 'position': 'GK'},
            'equipment': {'gloves_id': 101},
            'native_card': {'source_id': '50002'},
        }
        gk_sel_def = {'identity': {'package_id': 'p-gk1', 'card_id': 910001, 'person_id': 910002}}
        gk_sel_bind = {'asset_key': '910003'}

        gk_inh = {
            'identity': {'name': 'Keeper B', 'position': 'GK'},
            'equipment': {'gloves_id': None},
            'native_card': {'source_id': '50001'},
        }
        gk_inh_def = {'identity': {'package_id': 'p-gk2', 'card_id': 920001, 'person_id': 920002}}
        gk_inh_bind = {'asset_key': '920003'}

        out_gk, rep_gk = eq.compile_gloves(
            cpk,
            [gk_sel, gk_inh],
            [gk_sel_def, gk_inh_def],
            [gk_sel_bind, gk_inh_bind],
        )
        parsed = dict(struct.iter_unpack('<QQ', unwrap(out_gk)))
        # Untouched originals preserved
        self.assertEqual(parsed[50001], 111)
        self.assertEqual(parsed[50002], 201)
        # Selected GK
        self.assertEqual(parsed[910001], 101)
        self.assertEqual(parsed[910003], 101)
        self.assertEqual(rep_gk[0]['mode'], 'selected')
        self.assertEqual(rep_gk[0]['gloves_id'], 101)
        # Inherited GK preserves unmapped ID 111
        self.assertEqual(parsed[920001], 111)
        self.assertEqual(parsed[920003], 111)
        self.assertEqual(rep_gk[1]['mode'], 'template')
        self.assertEqual(rep_gk[1]['gloves_id'], 111)

    def test_patch_verified_accessories_bit_isolation(self):
        # Start with a 64-byte row with known sentinel pattern in untouched bits
        base_int = (0x123456789ABCDEF0) | (0x55555555555555 << 64) | (5 << 292) | (0xCAFEBABE << 320)
        row = base_int.to_bytes(64, 'little')

        # 1. All keep -> exact byte-identical return
        self.assertEqual(eq.patch_sleeves(row, {}), row)
        self.assertEqual(eq.patch_sleeves(row, {
            'sleeves': 'keep', 'socks': 'keep', 'shirt_tail': 'keep',
            'tights': 'keep', 'inner_shirt': 'keep', 'ankle_taping': 'keep',
            'player_gloves': 'keep', 'wrist_tape_left': 'keep', 'wrist_tape_right': 'keep',
        }), row)

        # 2. Patch all 7 new accessories + sleeves + socks and verify exact bit positions
        patched = eq.patch_sleeves(row, {
            'sleeves': 'long',          # bit 126..127 = 2
            'socks': 'short',           # bit 130..131 = 2
            'shirt_tail': 'tucked',     # bit 134 = 0
            'tights': 'long',           # bit 128..129 = 2
            'inner_shirt': 'highneck',  # bit 132..133 = 2
            'ankle_taping': 'on',       # bit 135 = 1
            'player_gloves': 'on',      # bit 136 = 1
            'wrist_tape_left': 'white', # bit 125 = 1, bit 120..121 = 1
            'wrist_tape_right': 'blue', # bit 124 = 1, bit 122..123 = 3
        })
        v = int.from_bytes(patched, 'little')
        self.assertEqual((v >> 120) & 3, 1)  # left wrist white
        self.assertEqual((v >> 122) & 3, 3)  # right wrist blue
        self.assertEqual((v >> 124) & 1, 1)  # right wrist on
        self.assertEqual((v >> 125) & 1, 1)  # left wrist on
        self.assertEqual((v >> 126) & 3, 2)  # long sleeves
        self.assertEqual((v >> 128) & 3, 2)  # long tights
        self.assertEqual((v >> 130) & 3, 2)  # short socks
        self.assertEqual((v >> 132) & 3, 2)  # highneck inner
        self.assertEqual((v >> 134) & 1, 0)  # shirt tucked in
        self.assertEqual((v >> 135) & 1, 1)  # ankle taping on
        self.assertEqual((v >> 136) & 1, 1)  # winter player gloves on

        # Every bit outside 120..136 MUST be 100% untouched!
        touched_mask = ((1 << 17) - 1) << 120
        self.assertEqual(v & ~touched_mask, base_int & ~touched_mask)

        # 3. Turn wrist tape off clears switch bit cleanly
        off_row = eq.patch_sleeves(patched, {'wrist_tape_left': 'off', 'wrist_tape_right': 'off'})
        v_off = int.from_bytes(off_row, 'little')
        self.assertEqual((v_off >> 125) & 1, 0)
        self.assertEqual((v_off >> 124) & 1, 0)

        # 4. Invalid option rejected
        for field in ('shirt_tail', 'tights', 'inner_shirt', 'ankle_taping', 'player_gloves', 'wrist_tape_left', 'wrist_tape_right'):
            with self.assertRaises(ValueError):
                eq.validate({field: 'bogus'})


if __name__ == '__main__':
    unittest.main()
