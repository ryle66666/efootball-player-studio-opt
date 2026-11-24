"""Exact personal front overrides, independent of old series filename rules."""

def front_overrides(card,asset_key,background_override=False):
    # None retains the native decoration; '' deliberately clears that layer.
    # F07 must always use the full portrait, never the mini '_' / '_m' image.
    front=[None]*10
    front[6]=str(asset_key)+'f'
    front[5]=front[8]=''  # F06 decoration and baked F09 date are suppressed.
    if card.get('preset_id'):front[3]=''  # F04 is already in the full portrait.
    if background_override:
        front=['']*10;front[0]=str(asset_key)+'bg';front[6]=str(asset_key)+'f'
    return front
