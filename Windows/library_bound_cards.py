"""Restore APK card presentation records to the desktop catalogue."""

from contextlib import closing
import hashlib
import json
import logging
from pathlib import Path
import re
import sqlite3
import zipfile

from personal_source import read as read_personal


KEY = 'apk_bound_cards_v2'
RELEASE = 'assets/gateway-release/release-manifest.json'
ART_NAME = re.compile(r'[0-9a-z_]{1,22}')
LEGACY_PORTRAIT_LAYOUT = 'ba4b50d1d74a8ee7f56dba9eb480756da779c50926e2d2f67a8668a6ae88b997'


def legacy_routes(payload):
    blob = bytes.fromhex(payload['blob_hex'])
    if hashlib.sha256(blob).hexdigest() != LEGACY_PORTRAIT_LAYOUT:
        return []

    def card_at(offset):
        return int.from_bytes(blob[offset:offset + 8], 'little')

    def portrait_at(offset):
        # Unreal short strings carry the byte count multiplied by two.
        size = blob[offset] // 2
        return blob[offset + 1:offset + 1 + size].decode('ascii').removesuffix('f')

    # This verified legacy installer keeps the short strings and donor IDs in
    # different literal tables. The last two portrait names use adjacent IDs.
    adjacent = card_at(3888)
    shared_donor = card_at(3712)
    rows = [
        (card_at(136), card_at(136), 168),
        (card_at(144), card_at(144), 200),
        (card_at(1048), card_at(1048), 1072),
        (card_at(3016), card_at(2544), 3048),
        (card_at(3024), card_at(2784), 3080),
        (adjacent, shared_donor, 3912),
        (adjacent + 1, shared_donor, 3944),
    ]
    return [{'cardId': cid, 'suiteDonorId': donor, 'portraitKey': portrait_at(offset)}
            for cid, donor, offset in rows]


def restore(db, archive, apk):
    release = json.loads(archive.read(RELEASE))
    personal = read_personal(archive, release)
    from apk_literals import module_reader
    from native_binding_source import literal_installer
    from native_diy_binding import route_layers

    stock = []
    try:
        root = module_reader(apk)('legends_v10')
        if 'diy_runtime' in root.co_names:
            stock = [route for layer in route_layers(root) for route in layer]
        _, payload = literal_installer(root)
        stock = [*legacy_routes(payload), *stock]
    except (OSError, KeyError, ValueError, zipfile.BadZipFile) as exc:
        logging.debug('No readable built-in DIY card bindings: %s', exc)

    cards = {}
    for route in [*stock, *personal.get('native_binding', {}).get('routes', [])]:
        cid, donor, portrait = route['cardId'], route['suiteDonorId'], route['portraitKey']
        if not (type(cid) is int and type(donor) is int and isinstance(portrait, str)
                and re.fullmatch(r'[0-9]{5,7}', portrait)):
            continue
        names = route.get('front_names')
        if (not isinstance(names, list) or len(names) != 10
                or any(name is not None and (not isinstance(name, str) or name and not ART_NAME.fullmatch(name))
                       for name in names)):
            names = [None] * 10
        cards[str(cid)] = {'suiteDonorId': donor, 'portraitKey': portrait, 'front_names': names}

    for definition in personal.get('players', []):
        cid = str(definition['identity']['card_id'])
        row = db.execute('SELECT payload,overall FROM players WHERE id=?', (cid,)).fetchone()
        if row is None:
            continue
        payload, overall = json.loads(row[0]), row[1]
        owned = definition['owned']
        rarity = owned.get('default_rarity')
        if type(rarity) is int and 0 <= rarity <= 4:
            payload['default_rarity'] = rarity
        base_overall = owned.get('_overall')
        if type(base_overall) is int and base_overall > 0:
            payload['_overall'] = overall = base_overall
        db.execute('UPDATE players SET payload=?,overall=? WHERE id=?',
                   (json.dumps(payload, ensure_ascii=False, separators=(',', ':')), overall, cid))

    db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (KEY, json.dumps(cards, separators=(',', ':'))))
    return cards


def load(resources):
    resources = Path(resources)
    database = resources / 'library.sqlite3'
    if not (resources / 'import-report.json').is_file():
        with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            row = db.execute('SELECT value FROM meta WHERE key=?', (KEY,)).fetchone()
            return json.loads(row[0]) if row else {}
    with closing(sqlite3.connect(database)) as db, db:
        row = db.execute('SELECT value FROM meta WHERE key=?', (KEY,)).fetchone()
        if row:
            return json.loads(row[0])
        from apk_sources import path_for_resources, packages_for_resources
        from resource_set import Reader

        report = json.loads((resources / 'import-report.json').read_text('utf8'))
        original = report.get('apk')
        try:
            apk = original if original and Path(original).is_file() else path_for_resources(resources)
            with Reader(apk, packages_for_resources(resources)) as archive:
                return restore(db, archive, apk)
        except (OSError, KeyError, ValueError, zipfile.BadZipFile) as exc:
            db.rollback()
            logging.warning('Could not restore APK card bindings: %s', exc)
            return {}


def front_name(route, slot):
    names = route and route.get('front_names')
    return names[int(slot[1:]) - 1] if names else None


def rarity(payload, route):
    value = payload.get('default_rarity')
    if value is None:
        return None if route and route.get('portraitKey') else 1
    return max(0, min(5, int(value) + 1))
