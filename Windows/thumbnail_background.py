"""The large card's background layers, flattened for the native small card."""
from pathlib import Path
import io,re,subprocess,uuid
from PIL import Image,ImageChops

REVISION='linked-card-thumbnail-v1'
ASSET=re.compile(r'[a-f0-9]{64}\.(?:png|jpg|jpeg|webp|webm|mp4|mov)')
MAX_CACHED_PIXELS = 2 * 1024 * 1024

def poster_time(layer):
    duration=layer.get('duration')
    return min(.8,duration/3) if type(duration) in (int,float) and duration>0 else .8

def desktop_frame(library,path,seconds):
    tool=Path(library.ffmpeg)
    if not tool.is_file():raise ValueError('缺少卡面视频工具，无法生成联动小头像')
    for moment in (seconds,0):
        result=subprocess.run([str(tool),'-hide_banner','-loglevel','error','-ss',str(moment),'-i',str(path),
            '-frames:v','1','-f','image2pipe','-vcodec','png','pipe:1'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            timeout=30,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode==0 and result.stdout:
            with Image.open(io.BytesIO(result.stdout)) as im:return im.convert('RGBA')
    raise ValueError('无法读取卡面背景动画的静态帧，请重新载入卡面')


def prepare_library_cover(ffmpeg, video, cover, *, demuxer=None):
    """Make a visible still during resource preparation."""
    if cover.is_file() or not ffmpeg.is_file():
        return cover.is_file()
    source = ['-f', demuxer, '-i', 'pipe:0'] if demuxer else ['-i', str(video)]
    command = [str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-threads', '1',
               '-filter_threads', '1', *source]

    def decode(options):
        result = subprocess.run(
            [*command, *options, '-threads', '1', 'pipe:1'],
            input=video if demuxer else None,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=8,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        return result.stdout if result.returncode == 0 else b''

    def png(frame):
        return decode(['-vf', f'select=eq(n\\,{frame})', '-frames:v', '1',
                       '-f', 'image2pipe', '-vcodec', 'png'])

    def visible(sample):
        return sum(max(pixel) >= 24 for pixel in sample.getdata()) * 50 >= sample.width * sample.height

    poster = png(5)
    if poster:
        with Image.open(io.BytesIO(poster)) as image, image.convert('RGB') as sample:
            sample.thumbnail((32, 32))
            if not visible(sample):
                poster = b''
    if not poster:
        # Scan small samples to catch short clips and fade-ins between fixed frame numbers.
        side = 24
        frame_bytes = side * side * 3
        samples = decode(['-vf', f'scale={side}:{side}', '-frames:v', '1200',
                          '-fps_mode', 'passthrough', '-f', 'rawvideo', '-pix_fmt', 'rgb24'])
        for frame, offset in enumerate(range(0, len(samples) - frame_bytes + 1, frame_bytes)):
            with Image.frombytes('RGB', (side, side), samples[offset:offset + frame_bytes]) as sample:
                if visible(sample):
                    poster = png(frame)
                    break
    if not poster:
        return False
    cover.parent.mkdir(parents=True, exist_ok=True)
    temporary = cover.with_name('.cover-' + uuid.uuid4().hex)
    try:
        temporary.write_bytes(poster)
        temporary.replace(cover)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def layer_image(library,layer,size: tuple[int, int] | None = None):
    asset=layer.get('asset_id','')
    if not ASSET.fullmatch(asset):raise ValueError('卡面背景资源编号无效')
    path=library.store.root/'assets'/asset
    if not path.is_file():raise ValueError('卡面背景资源缺失，请重新载入卡面')
    seconds=poster_time(layer);key=(asset,layer.get('kind'),seconds,size)
    with library.thumbnail_lock:
        cache=library.thumbnail_frames
        if key in cache:cache.move_to_end(key);return cache[key].copy()
        if layer.get('kind')=='video':image=library.thumbnail_video_frame(path,seconds)
        else:
            with Image.open(path) as im:
                image = (im.resize(size, Image.Resampling.LANCZOS)
                         if size is not None and im.mode == 'RGBA' else im.convert('RGBA'))
        if size is not None and image.size != size:
            full = image
            image = full.resize(size, Image.Resampling.LANCZOS)
            full.close()
        pixels = image.width * image.height
        if pixels <= MAX_CACHED_PIXELS:
            while (len(cache) >= 8 or
                   pixels + sum(frame.width * frame.height for frame in cache.values()) > MAX_CACHED_PIXELS):
                cache.popitem(last=False)[1].close()
            cache[key] = image.copy()
        return image

def background(project,library,size=512):
    appearance=project['appearance'];card=project.get('native_card') or {}
    # Match the large-card renderer's layers below its replacement portrait.
    order=({'f01':0,'f02':1,'f04':2,'f03':3,'f05':4,'f07':5,'portrait':6}
           if card.get('preset_id') else {'f01':0,'f02':1,'portrait':2})
    custom=card.get('custom_motion') or {}
    mini_preview=(custom.get('mini',{}).get('preview') or {}).get('asset_id')
    layers=([{'slot':'f01','kind':'image','asset_id':appearance['background_image']}]if appearance.get('card_background_override')and appearance.get('background_image')else
            [{'slot':'f02','kind':'video','asset_id':mini_preview}] if mini_preview else
            [l for l in card.get('layers',[]) if l.get('slot') in order and order[l['slot']]<order['portrait']])
    if not layers:
        asset=appearance.get('background_image') or (project.get('native_art',{}).get('background') or {}).get('asset_id')
        if asset:layers=[{'asset_id':asset,'kind':'image','slot':'f01'}]
    result=Image.new('RGBA',(size,size),appearance.get('background','#101a2c'))
    for layer in sorted(layers,key=lambda l:order.get(l.get('slot'),0)):
        image=layer_image(library,layer,result.size)
        mode='source-over' if layer.get('slot')=='f01' else layer.get('blend','source-over')
        if mode in ('screen','multiply'):
            rgb=getattr(ImageChops,mode)(result.convert('RGB'),image.convert('RGB')).convert('RGBA')
            rgb.putalpha(image.getchannel('A'));image=rgb
        elif mode!='source-over':raise ValueError('小头像背景不支持此图层混合方式：'+mode)
        result.alpha_composite(image)
    return result
