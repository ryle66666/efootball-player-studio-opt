"""Conservative in-place-identity recompression of authorized custom textures.

Ownership is the caller's responsibility: never scan stock/incremental content
with this primitive. A positive colour usage must come from the exact custom
art binding, not a guessed filename. Already GPU-compressed pixels are never
decoded/recompressed. Rejected/unknown layouts return the identical raw bytes.
"""
import hashlib,math,struct

REVISION='custom-inline-bgra-etc2-v1'
COLOUR_USES=frozenset(('colour','skin','portrait','card','mask'))
# FPackageObjectIndex::FromScriptPath('/Script/Engine/Texture2D'), lower-case
# UTF-16 CityHash64 with the script-import tag. Read-only current stock/custom
# Texture2D exports independently match this value; meshes/materials do not.
TEXTURE2D_CLASS_INDEX=0x5b93bca796d1fa6f


def _sha(raw):return hashlib.sha256(raw).hexdigest()


def _identity(raw,logical_name):
    from native_iostore import package_names,package_id
    if not isinstance(logical_name,str)or not logical_name.startswith('/Game/')or '\\'in logical_name or '..'in logical_name.split('/'):
        raise ValueError('Custom texture logical package name is invalid')
    if not isinstance(raw,bytes)or len(raw)<64:raise ValueError('Custom texture package is incomplete')
    h=struct.unpack_from('<16I',raw)
    if h[6]!=64 or h[9]<16 or h[9]%8 or not 64<=h[10]<=h[11]<=h[12]<=h[13]<=h[13]+h[14]<=len(raw):
        raise ValueError('Custom texture package summary is unsupported')
    names=package_names(raw);index=h[0]&0x3fffffff
    if index>=len(names)or names[index]!=logical_name:
        raise ValueError('Authorized custom texture does not match its package identity')
    return names,h,package_id(logical_name)


def _properties(raw):
    # Same strict, read-only property parser as the existing GLB texture path.
    from native_head_materials import _texture_fields
    names,h,fields,terminator=_texture_fields(raw);values={}
    for name,kind,enum,begin,end,payload,length in fields:
        if name not in('CompressionSettings','LODGroup','SRGB'):continue
        if name in values:raise ValueError('Duplicate native texture policy property')
        if kind=='ByteProperty'and length==8:
            ident,number=struct.unpack_from('<2I',raw,payload)
            if ident>=len(names)or number:raise ValueError('Invalid native texture policy enum')
            values[name]=names[ident]
        elif name=='SRGB'and kind=='BoolProperty'and length==0:
            value=raw[begin+24]
            if value not in(0,1):raise ValueError('Invalid native texture colour-space flag')
            values[name]=bool(value)
        else:raise ValueError('Unverified native texture policy property')
    compression=values.get('CompressionSettings')
    if compression is not None and compression not in('TC_Default','TC_EditorIcon'):
        return values,'non_colour_compression_setting'
    group=values.get('LODGroup','').lower()
    if any(word in group for word in('normal','specular','lightmap','shadowmap','ieslight','bokeh','data','rendertarget')):
        return values,'non_colour_texture_group'
    return values,None


def _layout(raw,h):
    from native_texture_write import platform_stream
    if h[12]-h[11]!=72:raise ValueError('Only single-export Texture2D packages are supported')
    layout=platform_stream(raw)
    if layout['start']<h[13]+h[14]+8:raise ValueError('Texture platform prefix is incomplete')
    absolute_delta=struct.unpack_from('<Q',raw,h[11])[0]-(h[13]+h[14])
    if struct.unpack_from('<q',raw,layout['start']-8)[0]!=len(raw)-8+absolute_delta:
        raise ValueError('Texture platform skip offset is unsupported')
    previous=None
    for mip in layout['mips']:
        dimensions=(mip['width'],mip['height'])
        if previous and dimensions!=(max(1,previous[0]//2),max(1,previous[1]//2)):
            raise ValueError('Original serialized mip dimensions are not a continuous chain')
        previous=dimensions
        if mip['flags']not in(0x40,0x48,0x2040,0x2048):
            raise ValueError('Only verified inline mip storage is supported')
        if mip['offset']!=mip['data_at']+absolute_delta:
            raise ValueError('Original inline mip offset is inconsistent')
        if len(raw[mip['data_at']:mip['data_at']+mip['bytes']])!=mip['bytes']:
            raise ValueError('Original inline mip data is incomplete')
    layout['absolute_delta']=absolute_delta
    return layout


def _rewrite(raw,h,before,fmt,blocks):
    """Keep name-map/imports/public export hashes/properties/FirstMip unchanged.

    UE serializes the actual platform PixelFormat as FString. Old stock hair
    packages already contain a stale PF name-map entry; changing that unused
    name is unnecessary and could change unrelated hashes or property indices.
    """
    start=before['start'];absolute_delta=before['absolute_delta'];encoded=fmt.encode('ascii')+b'\0'
    if len(blocks)!=len(before['mips']):raise ValueError('Encoded mip count changed')
    body=bytearray(struct.pack('<4I',before['width'],before['height'],1,len(encoded))+encoded+
        struct.pack('<2I',before['first'],len(blocks)))
    for mip,data in zip(before['mips'],blocks):
        w,hh=mip['width'],mip['height'];expected=((w+3)//4)*((hh+3)//4)*(8 if fmt=='PF_ETC2_RGB'else 16)
        if fmt not in('PF_ETC2_RGB','PF_ETC2_RGBA')or len(data)!=expected:raise ValueError('Encoded ETC2 byte count changed')
        wide=bool(mip['flags']&0x2000);descriptor=24 if wide else 16
        position=start+len(body)+8+descriptor
        body.extend(struct.pack('<2I',1,mip['flags']))
        body.extend(struct.pack('<QQq'if wide else'<IIq',len(data),len(data),position+absolute_delta))
        body.extend(data);body.extend(struct.pack('<3I',w,hh,1))
    # Preserve the original VT=false / None terminator bytes and name index.
    body.extend(raw[-12:]);result=bytearray(raw[:start]+body)
    struct.pack_into('<q',result,start-8,len(result)-8+absolute_delta)
    struct.pack_into('<Q',result,h[11]+8,struct.unpack_from('<Q',raw,h[11]+8)[0]+len(result)-len(raw))
    after=_layout(result,h)
    if after['format']!=fmt or after['width']!=before['width']or after['height']!=before['height']or after['first']!=before['first']:
        raise ValueError('Recompressed platform dimensions/FirstMip changed')
    # These are the only two non-platform-data fields allowed to change.
    prefix=bytearray(result[:start]);original=bytearray(raw[:start])
    for pos in(h[11]+8,start-8):prefix[pos:pos+8]=original[pos:pos+8]
    if prefix!=original or result[-12:]!=raw[-12:]:raise ValueError('Recompression changed non-pixel package data')
    for old,new,data in zip(before['mips'],after['mips'],blocks):
        if (new['width'],new['height'],new['flags'])!=(old['width'],old['height'],old['flags']):
            raise ValueError('Recompressed mip shape/storage changed')
        if result[new['data_at']:new['data_at']+new['bytes']]!=data:raise ValueError('Recompressed mip readback differs')
    return bytes(result),after


def recompress(raw,logical_name,*,usage='unknown',alpha_cutoff=.5,cache_dir=None,native_folder=None):
    """Return (bytes, report), preserving rejected/unsupported resources exactly.

    No file, package store, mesh or material is modified here. The caller must
    retain the existing container package-store entry, imports and dependencies.
    All *serialized* mip levels and their FirstMip/platform dimensions survive;
    unavailable/external bulk, >4K or unknown layouts stay byte-identical.
    """
    if not isinstance(alpha_cutoff,(int,float))or not math.isfinite(alpha_cutoff)or not 0<=alpha_cutoff<=1:
        raise ValueError('Texture alpha cutoff must be finite and within 0..1')
    names,h,pid=_identity(raw,logical_name)
    report={'revision':REVISION,'package':logical_name,'package_id':pid,'usage':usage,'alpha_cutoff':alpha_cutoff,
        'status':'preserved','changed':False,'source_sha256':_sha(raw),'source_package_bytes':len(raw),
        'result_sha256':_sha(raw),'result_package_bytes':len(raw),'in_game_tested':False}
    def keep(reason,detail=None):
        report['reason']=reason
        if detail:report['detail']=str(detail)
        return raw,report
    if h[12]-h[11]!=72 or struct.unpack_from('<Q',raw,h[11]+32)[0]!=TEXTURE2D_CLASS_INDEX:
        return keep('not_supported_texture2d_export')
    if usage not in COLOUR_USES:return keep('unverified_or_non_colour_usage')
    try:
        from native_texture_write import platform_stream
        # Inspect the real platform format before requiring inline bulk. A
        # stock-cloned custom tattoo may retain external ETC2 data: it is an
        # already-compressed no-op, never an external-BGRA decoding attempt.
        before=platform_stream(raw);report.update(source_format=before['format'],platform_dimensions=[before['width'],before['height']],
            first_mip=before['first'],mip_dimensions=[[m['width'],m['height']]for m in before['mips']])
        if before['format']!='PF_B8G8R8A8':return keep('already_compressed_or_non_bgra_source')
        if any(not(m['flags']&0x40)for m in before['mips']):return keep('external_bgra_bulk_unverified')
        before=_layout(raw,h)
        policy,reason=_properties(raw);report['native_policy']=policy
        if reason:return keep(reason)
        if any(max(m['width'],m['height'])>4096 for m in before['mips']):return keep('encoder_dimension_limit')
        from PIL import Image
        images=[]
        for mip in before['mips']:
            if mip['bytes']!=mip['width']*mip['height']*4:return keep('bgra_byte_count_mismatch')
            pixels=raw[mip['data_at']:mip['data_at']+mip['bytes']]
            images.append(Image.frombytes('RGBA',(mip['width'],mip['height']),pixels,'raw','BGRA'))
        from texture_codec import encode_mips
        fmt,blocks,details=encode_mips(images,usage,alpha_cutoff=alpha_cutoff,cache_dir=cache_dir,native_folder=native_folder,
            color_space='srgb'if policy.get('SRGB',True)else'linear',source_kind='lossless_rgba')
        report['codec']=details
        if fmt=='PF_B8G8R8A8':return keep(details.get('fallback_reason','codec_kept_bgra'))
        if fmt not in('PF_ETC2_RGB','PF_ETC2_RGBA'):return keep('unsupported_encoder_result')
        output,after=_rewrite(raw,h,before,fmt,blocks)
        if len(output)>=len(raw):return keep('no_package_size_gain')
        report.update(status='compressed',changed=True,result_format=fmt,result_sha256=_sha(output),
            result_package_bytes=len(output),saved_package_bytes=len(raw)-len(output),mip_count_preserved=True,
            platform_and_first_mip_preserved=True,non_pixel_prefix_preserved=True,package_identity_preserved=True,
            package_store_entry_must_be_retained=True,imports_exports_and_properties_preserved=True)
        return output,report
    except (ValueError,struct.error,IndexError,KeyError,MemoryError)as exc:
        return keep('unsupported_layout_or_failed_readback',exc)
