"""National-team card flag (Player.bin bit 626) follows the card's club.

Evidence (2026-11-11, V17 APK, read-only): in dt200 every one of the 700
clubs with an ID > 0 has a single value of bit 626; the 37 national-team
IDs are all 1. Club 0 (no club) is mixed and therefore never decided here.
The live dt870 already carries three hand-made cards that break the rule
(Thierry Henry at Arsenal = 1; Di Maria and Maradona at Argentina = 0), so
the club value is the clear majority, not unanimity.

The bit is not one of native-player-fields.json, so player_record() used to
inherit it from the donor: a Real Madrid card built from "Brazil 2002" stayed
a national-team card (no club crest, default kit in the draw animation).
"""
BIT = 626
MIN_ROWS = 3
MIN_SHARE = 0.95


def club_kinds(original_players, club_field):
    """club id -> flag for clubs whose original cards clearly agree."""
    counts = {}
    mask = (1 << club_field['width']) - 1
    for raw in original_players.values():
        v = int.from_bytes(raw, 'little')
        club = (v >> club_field['bit']) & mask
        if club <= 0:
            continue
        pair = counts.setdefault(club, [0, 0])
        pair[(v >> BIT) & 1] += 1
    kinds = {}
    for club, (zero, one) in counts.items():
        total = zero + one
        wanted = 1 if one > zero else 0
        # Unanimous clubs are decided at any size; a split club needs enough rows.
        if min(zero, one) == 0 or (total >= MIN_ROWS and max(zero, one) / total >= MIN_SHARE):
            kinds[club] = wanted
    return kinds


def apply(record, club, kinds):
    """Return (record, report). Unknown or undecided clubs keep the donor value."""
    v = int.from_bytes(record, 'little')
    before = (v >> BIT) & 1
    if club not in kinds:
        return record, {'club': club, 'national_team_flag': before, 'source': 'donor_kept_unknown_club'}
    wanted = kinds[club]
    report = {'club': club, 'national_team_flag': wanted, 'source': 'original_club_rule'}
    if wanted == before:
        return record, report
    v = (v & ~(1 << BIT)) | (wanted << BIT)
    report['corrected_from'] = before
    return v.to_bytes(len(record), 'little'), report
