"""Read tagged UE4 material instances from registered native packages."""
import math
from pathlib import PurePosixPath
import struct

MATERIAL_DECODER_REVISION = 'native-material-properties-v1'
MATERIAL_VALIDATION = 'embedded-base-color-v1'
COLOR_PARAMETERS = ('basecolormap', 'basecolor', 'diffuse', 'diffusemap')


def _header(raw):
    if len(raw) < 64:
        raise ValueError('材质资源包头不完整')
    header = struct.unpack_from('<16I', raw)
    if not 64 <= header[11] <= header[12] <= len(raw) or (header[12] - header[11]) % 72:
        raise ValueError('材质资源导出表无效')
    if not (64 <= header[6] <= header[6] + header[7] <= header[8]
            and header[9] >= 8 and header[9] % 8 == 0
            and header[8] + header[9] <= header[10] <= header[11]):
        raise ValueError('材质资源名称表无效')
    return header


def _names(raw, header):
    position, end = header[6], header[6] + header[7]
    count = header[9] // 8 - 1
    if count > header[7] // 2:
        raise ValueError('材质资源名称数量无效')
    result = []
    for _ in range(count):
        if position + 2 > end:
            raise ValueError('材质资源名称表不完整')
        first, second = raw[position:position + 2]
        position += 2
        wide = bool(first & 128)
        if wide:
            position += position % 2
        size = (((first & 127) << 8) | second) * (2 if wide else 1)
        if position + size > end:
            raise ValueError('材质资源名称长度越界')
        result.append(raw[position:position + size].decode('utf-16-le' if wide else 'utf8'))
        position += size
    return result


def _fname(raw, at, names, end):
    if at < 0 or at + 8 > end:
        raise ValueError('材质属性名称越界')
    index, number = struct.unpack_from('<II', raw, at)
    if index >= len(names) or number > 1_000_000:
        raise ValueError('材质属性名称无效')
    name = names[index]
    return name if not number else name + '_' + str(number - 1)


def _tag(raw, at, names, end):
    name = _fname(raw, at, names, end)
    if name == 'None':
        return {'name': name, 'end': at + 8}
    kind = _fname(raw, at + 8, names, end)
    if at + 24 > end:
        raise ValueError('材质属性标签不完整')
    size, array_index = struct.unpack_from('<II', raw, at + 16)
    position = at + 24
    metadata = {'StructProperty': 24, 'ByteProperty': 8, 'EnumProperty': 8,
                'ArrayProperty': 8, 'MapProperty': 16, 'SetProperty': 8,
                'BoolProperty': 1}.get(kind, 0)
    if position + metadata >= end:
        raise ValueError('材质属性元数据越界')
    row = {'name': name, 'type': kind, 'size': size, 'array_index': array_index}
    if kind == 'StructProperty':
        row['struct'] = _fname(raw, position, names, end)
    elif kind == 'ArrayProperty':
        row['inner'] = _fname(raw, position, names, end)
    elif kind == 'BoolProperty':
        if raw[position] not in (0, 1):
            raise ValueError('材质开关属性无效')
        row['bool'] = bool(raw[position])
    position += metadata
    flag = raw[position]
    if flag not in (0, 1):
        raise ValueError('材质属性 GUID 标记无效')
    position += 1 + 16 * flag
    if size > end - position:
        raise ValueError('材质属性数据越界')
    row.update(payload=position, end=position + size)
    return row


class _MaterialReader:
    def __init__(self, raw, load, references):
        self.raw = raw
        self.header = _header(raw)
        self.names = _names(raw, self.header)
        self.load = load
        self.references = references
        start, end = self.header[10:12]
        if not 64 <= start <= end or (end - start) % 8:
            raise ValueError('材质对象引用表无效')
        self.imports = [value[0] for value in struct.iter_unpack('<Q', raw[start:end])]
        self.objects = None

    def object_path(self, value):
        if value == 0:
            return {}
        if value >= 0 or -value > len(self.imports):
            raise ValueError('材质对象引用索引无效')
        wanted = self.imports[-value - 1]
        if self.objects is None:
            self.objects = {}
            for path in dict.fromkeys(name for name in self.names if name.startswith('/Game/')):
                if path not in self.references:
                    try:
                        dependency = self.load(path)
                        header = _header(dependency)
                        names = _names(dependency, header)
                        exports = {}
                        for at in range(header[11], header[12], 72):
                            name_index, number = struct.unpack_from('<II', dependency, at + 16)
                            if name_index & 0x3fffffff >= len(names):
                                raise ValueError('材质依赖资源名称越界')
                            name = names[name_index & 0x3fffffff]
                            if number:
                                name += '_' + str(number - 1)
                            identity = struct.unpack_from('<Q', dependency, at + 56)[0]
                            if identity != 0xffffffffffffffff:
                                exports[identity] = path + '.' + name
                        self.references[path] = exports
                    except (OSError, KeyError, ValueError):
                        self.references[path] = {}
                for identity, target in self.references[path].items():
                    previous = self.objects.setdefault(identity, target)
                    if previous != target:
                        raise ValueError('材质依赖资源对象标识重复')
        target = self.objects.get(wanted)
        if not target:
            raise ValueError('材质引用的资源对象未找到：' + str(value))
        return {'ObjectPath': target}

    def properties(self, at, end, depth=0):
        if depth > 12:
            raise ValueError('材质属性嵌套过深')
        result = {}
        for _ in range(4096):
            row = _tag(self.raw, at, self.names, end)
            at = row['end']
            if row['name'] == 'None':
                return result, at
            key, kind, offset, size = row['name'], row['type'], row['payload'], row['size']
            if row['array_index']:
                continue
            value = None
            if kind == 'NameProperty' and size == 8:
                value = _fname(self.raw, offset, self.names, row['end'])
            elif kind == 'ObjectProperty' and size == 4:
                value = self.object_path(struct.unpack_from('<i', self.raw, offset)[0])
            elif kind == 'FloatProperty' and size == 4:
                value = struct.unpack_from('<f', self.raw, offset)[0]
                if not math.isfinite(value):
                    raise ValueError('材质浮点属性不是有限值')
            elif kind == 'IntProperty' and size == 4:
                value = struct.unpack_from('<i', self.raw, offset)[0]
            elif kind == 'BoolProperty':
                value = row['bool']
            elif kind in ('ByteProperty', 'EnumProperty') and size == 8:
                value = _fname(self.raw, offset, self.names, row['end'])
            elif kind == 'ByteProperty' and size == 1:
                value = self.raw[offset]
            elif kind == 'StructProperty' and row['struct'] in (
                    'MaterialParameterInfo', 'MaterialInstanceBasePropertyOverrides'):
                value, last = self.properties(offset, row['end'], depth + 1)
                if last != row['end']:
                    raise ValueError('材质结构属性长度不符')
            elif key == 'TextureParameterValues' and kind == 'ArrayProperty':
                if row['inner'] != 'StructProperty' or size < 4:
                    raise ValueError('材质贴图参数数组无效')
                count = struct.unpack_from('<I', self.raw, offset)[0]
                if count > 256:
                    raise ValueError('材质贴图参数过多')
                value = []
                position = offset + 4
                if count or position < row['end']:
                    inner = _tag(self.raw, position, self.names, row['end'])
                    if inner.get('struct') != 'TextureParameterValue':
                        raise ValueError('材质贴图参数结构未适配')
                    position = inner['payload']
                    for _ in range(count):
                        parameter, position = self.properties(position, inner['end'], depth + 1)
                        if not isinstance(parameter.get('ParameterInfo', {}).get('Name'), str):
                            raise ValueError('材质贴图参数缺少名称')
                        value.append(parameter)
                    if position != inner['end']:
                        raise ValueError('材质贴图参数结构长度不符')
                if position != row['end']:
                    raise ValueError('材质贴图参数数组长度不符')
            if value is not None:
                if key in result:
                    raise ValueError('材质属性重复：' + key)
                result[key] = value
        raise ValueError('材质属性数量超出限制')


def material_json(raw, load, references=None):
    """Decode one cooked MaterialInstanceConstant using serialized object IDs."""
    reader = _MaterialReader(raw, load, {} if references is None else references)
    header = reader.header
    if header[12] - header[11] != 72 or 'MaterialInstanceConstant' not in reader.names:
        raise ValueError('增量 JSON 读取目前支持单一材质实例资源')
    start = header[13] + header[14]
    size = struct.unpack_from('<Q', raw, header[11] + 8)[0]
    if start < header[12] or not 0 < size <= len(raw) - start:
        raise ValueError('材质实例导出范围无效')
    properties, _ = reader.properties(start, start + size)
    index = struct.unpack_from('<I', raw, header[11] + 16)[0] & 0x3fffffff
    if index >= len(reader.names):
        raise ValueError('材质实例名称无效')
    return [{'Type': 'MaterialInstanceConstant', 'Name': reader.names[index], 'Properties': properties}]


def package_path(value):
    value = value.removeprefix('../../../').split('.', 1)[0]
    if value.startswith('/Game/'):
        value = 'PesMobile/Content/' + value[6:]
    if not value.endswith('.uasset'):
        value += '.uasset'
    if not value.startswith('PesMobile/Content/') or '\\' in value or '..' in PurePosixPath(value).parts:
        raise ValueError('材质资源路径无效')
    return value


def material_properties(path, load):
    chain = []
    visited = set()
    while path:
        path = package_path(path)
        if path in visited or len(chain) >= 16:
            raise ValueError('原版材质父级引用循环或过深：' + path)
        visited.add(path)
        exports = load(path)
        if not isinstance(exports, list) or any(not isinstance(entry, dict) for entry in exports):
            raise ValueError('原版材质 JSON 结构无效：' + path)
        material = next((entry for entry in exports if entry.get('Type') in ('MaterialInstanceConstant', 'Material')), None)
        if material is None or not isinstance(material.get('Properties'), dict):
            raise ValueError('原版材质属性无法读取：' + path)
        props = material['Properties']
        chain.append(props)
        own = props.get('TextureParameterValues', [])
        if any(entry.get('ParameterInfo', {}).get('Name', '').casefold() in COLOR_PARAMETERS
               and entry.get('ParameterValue', {}).get('ObjectPath', '').startswith('/Game/') for entry in own):
            break
        parent = (props.get('Parent') or {}).get('ObjectPath', '')
        path = parent if parent.startswith('/Game/') else None
    parameters = {}
    overrides = {}
    for props in reversed(chain):
        overrides.update(props.get('BasePropertyOverrides') or {})
        for entry in props.get('TextureParameterValues', []):
            key = entry.get('ParameterInfo', {}).get('Name', '').casefold()
            if key and entry.get('ParameterValue', {}).get('ObjectPath'):
                parameters[key] = entry
    return {'TextureParameterValues': list(parameters.values()), 'BasePropertyOverrides': overrides}


def validate_face_textures(path):
    """Require an embedded base-colour image for every rendered face material."""
    from native_glb import Glb
    glb = Glb(path.read_bytes())
    used = {primitive.get('material', 0) for mesh in glb.doc.get('meshes', [])
            for primitive in mesh.get('primitives', [])}
    if not used:
        raise ValueError('原版脸模没有可用的材质')
    for index in used:
        try:
            if type(index) is not int or index < 0:
                raise ValueError('材质索引无效')
            material = glb.doc['materials'][index]
            texture = material['pbrMetallicRoughness']['baseColorTexture']
            texture_index = texture['index']
            if type(texture_index) is not int or texture_index < 0:
                raise ValueError('贴图索引无效')
            image_index = glb.doc['textures'][texture_index]['source']
            if type(image_index) is not int or image_index < 0:
                raise ValueError('图像索引无效')
            image = glb.doc['images'][image_index]
            view_index = image['bufferView']
            if type(view_index) is not int or view_index < 0:
                raise ValueError('图像数据索引无效')
            view = glb.doc['bufferViews'][view_index]
            offset, size = view.get('byteOffset', 0), view['byteLength']
            if type(offset)is not int or type(size)is not int or image.get('uri') or offset < 0 or size < 8 or offset + size > len(glb.binary):
                raise ValueError('内嵌贴图范围无效')
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ValueError('原版脸模材质缺少有效的内嵌 BaseColor 贴图：' + str(index)) from exc
    return len(used)
