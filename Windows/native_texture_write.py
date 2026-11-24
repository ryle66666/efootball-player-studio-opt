"""Strict UE4 cooked 2D texture stream writer; all bulk becomes inline."""
import re,struct
from native_iostore import package_names,rename_package

def platform_stream(raw):
    h=struct.unpack_from('<16I',raw);base=h[13]+h[14]
    for match in re.finditer(rb'PF_[A-Za-z0-9_]+\0',raw[base:base+8192]):
        at=base+match.start();fmt=match.group();start=at-16
        if start<base:continue
        w,hh,slices,length=struct.unpack_from('<4I',raw,start)
        if not 1<=w<=8192 or not 1<=hh<=8192 or slices!=1 or length!=len(fmt):continue
        pos=at+length
        first,count=struct.unpack_from('<2I',raw,pos);pos+=8
        if not 1<=count<=16 or first>=count:continue
        try:
            levels=[]
            for _ in range(count):
                cooked,flags=struct.unpack_from('<2I',raw,pos);pos+=8
                if cooked!=1:raise ValueError('Uncooked mip')
                wide=bool(flags&0x2000);size,stored,offset=struct.unpack_from('<QQq'if wide else'<IIq',raw,pos)
                pos+=24 if wide else 16
                if size!=stored or not 0<size<=128*1024**2:raise ValueError('Invalid mip size')
                data_at=pos
                if flags&0x40:pos+=size
                elif not flags&0x100:raise ValueError('Unsupported bulk storage')
                mw,mh,depth=struct.unpack_from('<3I',raw,pos);pos+=12
                if not 1<=mw<=w or not 1<=mh<=hh or depth!=1:raise ValueError('Invalid mip dimensions')
                levels.append({'width':mw,'height':mh,'bytes':size,'flags':flags,'offset':offset,'data_at':data_at})
            if struct.unpack_from('<I',raw,pos)[0]!=0:raise ValueError('Virtual textures have not been verified')
            if pos+12!=len(raw):raise ValueError('Unsupported texture trailing exports')
            return {'start':start,'format':fmt[:-1].decode(),'width':w,'height':hh,'first':first,'mips':levels}
        except (ValueError,struct.error):continue
    raise ValueError('Could not identify verified native 2D texture stream')

def write_texture(raw,name,images,fmt,blocks):
    if not images or len(images)!=len(blocks)or len(images)>16:raise ValueError('Texture mip count differs')
    for index,(image,data)in enumerate(zip(images,blocks)):
        w,h=image.size
        if not 1<=w<=4096 or not 1<=h<=4096:raise ValueError('Native texture dimensions exceed supported range')
        expected=w*h*4 if fmt=='PF_B8G8R8A8'else((w+3)//4)*((h+3)//4)*(8 if fmt=='PF_ETC2_RGB'else 16)
        if fmt not in ('PF_B8G8R8A8','PF_ETC2_RGB','PF_ETC2_RGBA')or len(data)!=expected:raise ValueError('Texture format and block bytes differ')
        if index and image.size!=(max(1,images[index-1].width//2),max(1,images[index-1].height//2)):raise ValueError('Texture mip dimensions changed strategy')
    platform_stream(raw) # Reject unknown layout before mutating names/properties.
    names=package_names(raw);changes={n:fmt for n in names if n.startswith('PF_')}
    raw,metadata=rename_package(raw,name,changes,extra_names=(fmt,))
    layout=platform_stream(raw);start=layout['start'];names=package_names(raw);h=struct.unpack_from('<16I',raw);base=h[13]+h[14]
    if (h[12]-h[11])//72!=1:raise ValueError('Only single-export texture packages have been verified')
    width,height=images[0].size
    if 'ImportedSize'in names and 'IntPoint'in names:
        marker=struct.pack('<2I',names.index('ImportedSize'),0);at=raw.find(marker,base,start)
        if at>=0:
            if (struct.unpack_from('<I',raw,at+8)[0]!=names.index('StructProperty')or struct.unpack_from('<I',raw,at+16)[0]!=8 or struct.unpack_from('<I',raw,at+24)[0]!=names.index('IntPoint')):raise ValueError('Native imported size layout changed')
            if raw[at+48]not in (0,1):raise ValueError('Native imported size GUID changed')
            updated=bytearray(raw);struct.pack_into('<2I',updated,at+49+(16 if raw[at+48]else 0),width,height);raw=bytes(updated)
    absolute_delta=struct.unpack_from('<Q',raw,h[11])[0]-base
    encoded=fmt.encode()+b'\0';body=bytearray(struct.pack('<4I',width,height,1,len(encoded))+encoded+struct.pack('<2I',0,len(images)))
    for image,data in zip(images,blocks):
        pixels_at=start+len(body)+24
        body.extend(struct.pack('<4Iq',1,0x48,len(data),len(data),pixels_at+absolute_delta))
        body.extend(data);body.extend(struct.pack('<3I',image.width,image.height,1))
    body.extend(struct.pack('<3I',0,names.index('None'),0))
    result=bytearray(raw[:start]+body)
    struct.pack_into('<q',result,start-8,len(result)-8+absolute_delta)
    struct.pack_into('<Q',result,h[11]+8,struct.unpack_from('<Q',raw,h[11]+8)[0]+len(result)-len(raw))
    check=platform_stream(result)
    if check['format']!=fmt or len(check['mips'])!=len(images):raise ValueError('Written cooked texture failed readback')
    for mip,image,data in zip(check['mips'],images,blocks):
        if (mip['width'],mip['height'])!=image.size or mip['offset']!=mip['data_at']+absolute_delta or result[mip['data_at']:mip['data_at']+len(data)]!=data:raise ValueError('Written texture mip offset/data failed readback')
    return bytes(result),metadata
