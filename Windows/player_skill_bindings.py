"""Separate the per-player data at the end of the installed match hook."""
import struct


HOOK = 'studio_skill_runtime_v2'
MAGIC = struct.pack('<Q', 0x535455444c574631)


def unpack(body_hex):
    body = bytearray.fromhex(body_hex)
    marker = body.find(MAGIC)
    if marker < 0 or body.find(MAGIC, marker+1) >= 0 or marker+16 > len(body):
        raise ValueError('球员技能编号表缺少有效的原生接口')
    count, relative = struct.unpack_from('<II', body, marker+8)
    offset = marker+relative if count else len(body)
    if count > 65536 or (count and offset < marker+16) or offset+count*16 != len(body) or (not count and relative):
        raise ValueError('球员技能编号表的范围无效')
    rows = dict(struct.iter_unpack('<QQ', body[offset:]))
    if len(rows) != count:
        raise ValueError('球员技能编号表中的卡号重复')
    prefix = body[:offset]
    prefix[marker+8:marker+16] = bytes(8)
    return prefix, marker, rows


def pack(prefix, marker, bindings):
    body = bytearray(prefix)
    if bindings:
        body.extend(bytes(-len(body) % 16))
        struct.pack_into('<II', body, marker+8, len(bindings), len(body)-marker)
        body.extend(b''.join(struct.pack('<QQ', key, value) for key, value in sorted(bindings.items())))
    return body.hex()
