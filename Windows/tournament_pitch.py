"""The game's squad-edit pitch (GamePlanCustomPitchLeft.uasset, 1080p layout units).

PositionArea's twelve Plate_* panels decide a dragged player's role in the
game's own position editor. Stored formations use position_x = depth 0..50
(own goal -> opponent goal) and position_y = side 0..100 (left -> right);
on the 1128x888 pitch that is px = y/100*1128, py = (1 - x/50)*888. Every
base position the game itself saved falls in the plate of its role.
"""
WIDTH, HEIGHT = 1128, 888
MOVE = (30, 30, 1068, 828)          # MoveRect: where a player's centre may go
# name: (left, top, width, height, wire role)
PLATES = {
    'CF': (228, 3, 672, 150, 12), 'ST': (228, 153, 672, 87, 11), 'OMF': (228, 240, 672, 117, 8),
    'CMF': (228, 357, 672, 117, 5), 'DMF': (228, 474, 672, 117, 4), 'CB': (228, 591, 672, 294, 1),
    'LWF': (3, 3, 225, 237, 9), 'LMF': (3, 240, 225, 351, 6), 'LSB': (3, 591, 225, 294, 2),
    'RWF': (900, 3, 225, 237, 10), 'RMF': (900, 240, 225, 351, 7), 'RSB': (900, 591, 225, 294, 3),
}
GK = 0


def to_px(position_x, position_y):
    return position_y / 100 * WIDTH, (1 - position_x / 50) * HEIGHT


def role_at(position_x, position_y):
    """Wire role of the plate under a base position, or None outside every plate."""
    px, py = to_px(position_x, position_y)
    for left, top, width, height, role in PLATES.values():
        if left <= px < left + width and top <= py < top + height:
            return role
    return None


def in_move_rect(position_x, position_y):
    px, py = to_px(position_x, position_y)
    left, top, width, height = MOVE
    return left <= px <= left + width and top <= py <= top + height


def layout():
    """JSON for the hub page (same numbers the server validates with)."""
    return {'width': WIDTH, 'height': HEIGHT, 'move': list(MOVE),
            'plates': [{'name': n, 'left': l, 'top': t, 'width': w, 'height': h, 'role': r} for n, (l, t, w, h, r) in PLATES.items()]}
