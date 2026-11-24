"""Lossless-input ETC2 encoding with independent decode/quality gates.

Only explicitly verified colour purposes opt in. Normals, packed material
channels and unknown uses stay BGRA8. No native compressed input is decoded
and recompressed by this interface. The caller supplies authoring RGBA/mips.
"""
from pathlib import Path
from contextlib import contextmanager
from contextvars import ContextVar
import ctypes,hashlib,io,json,os,platform,threading,uuid,zipfile

REVISION='etc2-source-rgba-v1'
ENCODER_COMMIT='3d716e1550023dc5ff8b602636af8b76584e0b66'
POLICY='colour-bias-rmse-mask3333-5000-thin-v1'
_SCOPE=ContextVar('studio_texture_compilation',default={})
_LIBRARIES={};_LOCK=threading.RLock()
COLOUR_USES={'colour','skin','portrait','card','tifo','emblem','mask'}

@contextmanager
def compilation_scope(cache_dir=None,reports=None,native_folder=None):
    token=_SCOPE.set({'cache_dir':cache_dir,'reports':reports,'native_folder':native_folder})
    try:yield
    finally:_SCOPE.reset(token)

def cache_identity(native_folder=None):
    result={'revision':REVISION,'encoder':ENCODER_COMMIT,'quality_policy':POLICY}
    try:result['backend']=_library()[1]
    except (OSError,ValueError,AttributeError):result['backend']='unavailable-preserve-bgra8'
    try:result['independent_decoder']=_decoder(native_folder if native_folder is not None else _SCOPE.get().get('native_folder'))[1]
    except (OSError,ValueError,AttributeError):result['independent_decoder']='unavailable-preserve-bgra8'
    return result

def _library():
    from studio_resources import bundled_resources
    path=os.environ.get('STUDIO_ETC2_CODEC')
    if not path and os.environ.get('STUDIO_CODECS'):path=str(Path(os.environ['STUDIO_CODECS']).with_name('libstudio_etc2.so'))
    if not path:path=str(bundled_resources()/'texture-codec/windows-x86_64/studio_etc2.dll')if os.name=='nt'else 'libstudio_etc2.so'
    with _LOCK:
        if path not in _LIBRARIES:
            library=ctypes.CDLL(path);version=library.studio_etc2_version;version.restype=ctypes.c_uint32
            if version()!=0x10001:raise ValueError('ETC2 encoder ABI differs')
            fn=library.studio_etc2_encode
            fn.argtypes=[ctypes.c_char_p,ctypes.c_size_t,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p,ctypes.c_size_t];fn.restype=ctypes.c_int
            identity={'commit':ENCODER_COMMIT,'abi':0x10001,'machine':platform.machine(),
                      'binary_sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest()if Path(path).is_file()else path}
            _LIBRARIES[path]=(library,identity)
        return _LIBRARIES[path]

def encode_rgba(rgba,width,height,alpha):
    if type(width)is not int or type(height)is not int or not 1<=width<=4096 or not 1<=height<=4096 or len(rgba)!=width*height*4:
        raise ValueError('ETC2 source dimensions/length differ')
    library,identity=_library();size=((width+3)//4)*((height+3)//4)*(16 if alpha else 8)
    output=ctypes.create_string_buffer(size)
    if library.studio_etc2_encode(rgba,len(rgba),width,height,int(bool(alpha)),output,size)!=1:raise ValueError('Native ETC2 encoding failed')
    return output.raw,identity

def _decoder(native_folder=None):
    if os.environ.get('STUDIO_CODECS'):
        path=Path(os.environ['STUDIO_CODECS']);library=ctypes.CDLL(str(path));fn=library.studio_blocks
        fn.argtypes=[ctypes.c_char_p,ctypes.c_size_t,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_void_p];fn.restype=ctypes.c_int
        def decode(data,w,h,fmt):
            from PIL import Image
            # The independent decoder's block writer needs padded storage;
            # crop only its logical image, never the authoring texture.
            out=ctypes.create_string_buffer(((w+3)//4*4)*((h+3)//4*4)*4)
            if fn(data,len(data),w,h,1 if fmt=='PF_ETC2_RGB'else 2,out)!=1:raise ValueError('Independent ETC2 decoder rejected data')
            return Image.frombytes('RGBA',(w,h),out.raw[:w*h*4],'raw','BGRA')
        return decode,{'name':'Texture2DDecoder','binary_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    path=Path(os.environ['STUDIO_DETEX'])if os.environ.get('STUDIO_DETEX')else Path(native_folder)/'Detex.dll'if native_folder else None
    if path is None or not path.is_file():raise ValueError('independent_decoder_unavailable')
    from native_texture import desktop_etc2_png
    from PIL import Image
    def decode(data,w,h,fmt):
        # Detex also writes full 4x4 edge blocks. Decode their padded extent,
        # then crop to the immutable logical dimensions for the quality gate.
        pw=(w+3)//4*4;ph=(h+3)//4*4
        result=Image.open(io.BytesIO(desktop_etc2_png({'data':data,'width':pw,'height':ph,'format':fmt},path.parent))).convert('RGBA')
        return result.crop((0,0,w,h))
    return decode,{'name':'Detex','binary_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}

def quality(source,decoded,usage,alpha_cutoff=.5):
    """Independent colour/alpha/coverage/one-pixel-strand acceptance metrics."""
    import numpy as np
    if source.size!=decoded.size:raise ValueError('Independent decoder changed dimensions')
    # Strip processing bounds temporary RAM independently of a 4K input. No
    # float32 copies of the complete image or per-pixel Python loops exist.
    w,h=source.size;pixels=w*h;thresholds=sorted(set((.3333,.5,float(alpha_cutoff))))
    sums={'rgb_squared':0,'visible':0,'alpha_squared':0,'composite':[0.,0.]}
    bias=np.zeros(3,dtype=np.int64);hist=np.zeros(256,dtype=np.int64);ahist=np.zeros(256,dtype=np.int64)
    unique_a=np.zeros(256,dtype=np.int64);unique_b=np.zeros(256,dtype=np.int64)
    cuts=[{'threshold':t,'before':0,'after':0,'flipped':0,'lost':0,'thin':0,'thin_lost':0}for t in thresholds]
    for y0 in range(0,h,128):
        y1=min(h,y0+128);a=np.asarray(source.crop((0,y0,w,y1)),dtype=np.int16);b=np.asarray(decoded.crop((0,y0,w,y1)),dtype=np.int16)
        visible=a[:,:,3]>=32;diff=(b[:,:,:3]-a[:,:,:3])[visible];sums['visible']+=int(np.count_nonzero(visible))
        sums['rgb_squared']+=int(np.sum(diff.astype(np.int32)**2,dtype=np.int64));bias+=np.sum(diff,axis=0,dtype=np.int64)
        hist+=np.bincount(np.abs(diff).ravel(),minlength=256)
        alpha=b[:,:,3]-a[:,:,3];sums['alpha_squared']+=int(np.sum(alpha.astype(np.int32)**2,dtype=np.int64))
        ahist+=np.bincount(np.abs(alpha).ravel(),minlength=256)
        unique_a+=np.bincount(a[:,:,3].ravel(),minlength=256);unique_b+=np.bincount(b[:,:,3].ravel(),minlength=256)
        af=a.astype(np.float32);bf=b.astype(np.float32)
        for index,backdrop in enumerate((0.,255.)):
            ca=af[:,:,:3]*(af[:,:,3:]/255)+backdrop*(1-af[:,:,3:]/255)
            cb=bf[:,:,:3]*(bf[:,:,3:]/255)+backdrop*(1-bf[:,:,3:]/255)
            sums['composite'][index]+=float(np.sum((ca-cb)**2,dtype=np.float64))
        alpha_halo=np.asarray(source.crop((0,max(0,y0-1),w,min(h,y1+1))).getchannel('A'))
        for row in cuts:
            threshold=row['threshold']*255;before=a[:,:,3]>=threshold;after=b[:,:,3]>=threshold
            halo=np.pad(alpha_halo>=threshold,((int(y0==0),int(y1==h)),(1,1)))
            neighbours=np.zeros(before.shape,dtype=np.uint8)
            for dy in range(3):
                for dx in range(3):neighbours+=halo[dy:dy+y1-y0,dx:dx+w]
            thin=before&(neighbours<=4)
            row['before']+=int(np.count_nonzero(before));row['after']+=int(np.count_nonzero(after));row['flipped']+=int(np.count_nonzero(before!=after))
            row['lost']+=int(np.count_nonzero(before&~after));row['thin']+=int(np.count_nonzero(thin));row['thin_lost']+=int(np.count_nonzero(thin&~after))
    quantile=lambda values:float(np.searchsorted(np.cumsum(values),max(1,int(np.ceil(values.sum()*.95)))))
    count=max(1,sums['visible']);rmse=float(np.sqrt(sums['rgb_squared']/(count*3)));p95=quantile(hist);bias=(bias/count).tolist()
    result={'rgb_rmse':rmse,'rgb_abs_p95':p95,'rgb_mean_bias':bias,'temporary_rows_max':130};fail=[]
    rgb_limit=8. if usage in ('skin','portrait','card','emblem')else 10.
    if rmse>rgb_limit or p95>24 or max(map(abs,bias))>3:fail.append('colour_error')
    result['alpha_rmse']=float(np.sqrt(sums['alpha_squared']/pixels));result['alpha_abs_p95']=quantile(ahist)
    if result['alpha_rmse']>6 or result['alpha_abs_p95']>12:fail.append('alpha_error')
    # Composite tests catch coloured/opaque fringes around transparent edges.
    comps=[float(np.sqrt(s/(pixels*3)))for s in sums['composite']]
    result['composite_rmse_black_white']=comps
    if max(comps)>rgb_limit:fail.append('transparent_edge_colour_error')
    result['cutoffs']=[]
    for row in cuts:
        lost=row['lost']/max(1,row['before']);thin_lost=row['thin_lost']/max(1,row['thin'])
        flipped=row['flipped']/pixels;coverage=(row['after']-row['before'])/pixels
        result['cutoffs'].append({'threshold':row['threshold'],'source_coverage':row['before']/pixels,
            'coverage_delta':coverage,'flipped_fraction':flipped,'foreground_lost_fraction':lost,
            'thin_foreground_pixels':row['thin'],'thin_lost_fraction':thin_lost})
        if flipped>.005 or abs(coverage)>.002 or lost>.005 or thin_lost>.005:fail.append('mask_coverage_or_thin_strand_loss')
    if np.count_nonzero(unique_a)>2 and np.count_nonzero(unique_b)<=2:fail.append('continuous_alpha_collapsed')
    result['accepted']=not fail;result['reasons']=sorted(set(fail));return result

def _cache_read(path,key):
    with zipfile.ZipFile(path)as archive:
        if archive.getinfo('manifest.json').file_size>1024*1024:raise ValueError('Texture cache manifest too large')
        meta=json.loads(archive.read('manifest.json'))
        if meta['key']!=key:raise ValueError('ETC2 cache identity differs')
        if meta['format']not in ('PF_B8G8R8A8','PF_ETC2_RGB','PF_ETC2_RGBA')or meta['report']['format']!=meta['format']or len(meta['mips'])>16:raise ValueError('ETC2 cache format differs')
        if meta['format']=='PF_B8G8R8A8':return [],meta['report']
        blocks=[]
        for index,row in enumerate(meta['mips']):
            if not 0<row['bytes']<=16*1024*1024 or archive.getinfo(str(index)+'.bin').file_size!=row['bytes']:raise ValueError('Texture cache mip too large')
            data=archive.read(str(index)+'.bin')
            if len(data)!=row['bytes']or hashlib.sha256(data).hexdigest()!=row['sha256']:raise ValueError('ETC2 cache checksum differs')
            blocks.append(data)
        return blocks,meta['report']

def _cache_write(folder,key,fmt,blocks,report):
    folder=Path(folder)
    if folder.is_symlink()or folder.is_junction():raise ValueError('Texture cache may not be a link')
    folder.mkdir(parents=True,exist_ok=True);target=folder/('etc2-'+key+'.zip');temporary=folder/('etc2-writing-'+uuid.uuid4().hex+'.tmp')
    meta={'key':key,'format':fmt,'report':report,'mips':[{'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()}for b in blocks]}
    try:
        with zipfile.ZipFile(temporary,'x',compression=zipfile.ZIP_STORED)as archive:
            archive.writestr('manifest.json',json.dumps(meta,sort_keys=True,separators=(',',':')))
            for index,data in enumerate(blocks):archive.writestr(str(index)+'.bin',data)
        temporary.replace(target)
        # Only our strictly named derived artifacts are eligible for eviction.
        import re
        entries=sorted((p for p in folder.glob('etc2-*.zip')if re.fullmatch(r'etc2-[a-f0-9]{64}\.zip',p.name)and not p.is_symlink()),key=lambda p:p.stat().st_mtime_ns,reverse=True)
        total=0
        for entry in entries:
            total+=entry.stat().st_size
            if total>512*1024*1024 and entry!=target:entry.unlink()
    finally:temporary.unlink(missing_ok=True)

def encode_mips(images,usage='unknown',*,cache_dir=None,native_folder=None,alpha_cutoff=.5,color_space='native',source_kind='lossless_rgba'):
    scope=_SCOPE.get();cache_dir=cache_dir if cache_dir is not None else scope.get('cache_dir')
    native_folder=native_folder if native_folder is not None else scope.get('native_folder')
    if not 0<=alpha_cutoff<=1:raise ValueError('Texture alpha cutoff must be within 0..1')
    report={**cache_identity(),'usage':usage,'dimensions':[list(i.size)for i in images],
            'color_space':color_space,'alpha_cutoff':alpha_cutoff,'source_kind':source_kind,'cache_hit':False}
    fmt='PF_B8G8R8A8';blocks=[];key=None
    try:
        if usage not in COLOUR_USES:report['fallback_reason']='unverified_material_channels'
        elif source_kind!='lossless_rgba':report['fallback_reason']='lossy_source_not_reencoded'
        else:
            library,encoder=_library();decode,decoder=_decoder(native_folder)
            alpha=any(i.getchannel('A').getextrema()[0]!=255 for i in images)
            wanted='PF_ETC2_RGBA'if alpha else'PF_ETC2_RGB'
            sources=[i.tobytes()for i in images]
            report.update(encoder=encoder,independent_decoder=decoder)
            cache_input={**report,'format':wanted,'source_hashes':[hashlib.sha256(p).hexdigest()for p in sources]}
            key=hashlib.sha256(json.dumps(cache_input,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            path=Path(cache_dir)/('etc2-'+key+'.zip')if cache_dir else None
            if path and path.is_file()and not path.is_symlink():
                try:
                    blocks,saved=_cache_read(path,key);cached_format=saved['format']
                    if saved['dimensions']!=report['dimensions']or len(blocks)!=(0 if cached_format=='PF_B8G8R8A8'else len(images)):raise ValueError('Cached mip chain differs')
                    if cached_format!='PF_B8G8R8A8':
                        for image,data in zip(images,blocks):
                            if len(data)!=((image.width+3)//4)*((image.height+3)//4)*(8 if cached_format=='PF_ETC2_RGB'else 16):raise ValueError('Cached block size differs')
                    report={**saved,'cache_hit':True};fmt=cached_format
                except (OSError,ValueError,KeyError,RuntimeError,zipfile.BadZipFile):report['cache_corrupt']=True
            if not report['cache_hit']:
                checks=[]
                for image,pixels in zip(images,sources):
                    data,_=encode_rgba(pixels,*image.size,alpha);blocks.append(data)
                    checks.append(quality(image,decode(data,*image.size,wanted),usage,alpha_cutoff))
                report['quality']=checks
                if all(c['accepted']for c in checks)and sum(map(len,blocks))<sum(map(len,sources)):fmt=wanted
                else:report['fallback_reason']='quality_gate'if not all(c['accepted']for c in checks)else'no_size_gain';blocks=[]
    except (OSError,ValueError,AttributeError,MemoryError)as exc:
        report['fallback_reason']='encoder_or_independent_decoder_unavailable';report['detail']=str(exc);blocks=[]
    report['format']=fmt;report['source_bytes']=sum(i.width*i.height*4 for i in images)
    if fmt=='PF_B8G8R8A8':blocks=[i.tobytes('raw','BGRA')for i in images]
    report['encoded_bytes']=sum(map(len,blocks))
    if key and cache_dir and not report['cache_hit']:
        try:_cache_write(cache_dir,key,fmt,blocks if fmt!='PF_B8G8R8A8'else[],report)
        except (OSError,ValueError):report['cache_write_failed']=True
    if scope.get('reports')is not None:scope['reports'].append(report)
    return fmt,blocks,report
