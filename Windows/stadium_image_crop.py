"""Non-destructive image framing, independent of world placement."""
import hashlib
import json
import math

DEFAULT={'fit':'cover','zoom':1.0,'x':0.0,'y':0.0}
REVISION='tifo-image-framing-v1'


def normalize(value=None):
    if value is None:return dict(DEFAULT)
    if not isinstance(value,dict)or set(value)-set(DEFAULT):raise ValueError('TIFO 图片调整格式无效')
    fit=value.get('fit','cover')
    if fit not in ('cover','contain'):raise ValueError('TIFO 图片适配方式无效')
    result={'fit':fit}
    for name,low,high,default in (('zoom',.1,8,1),('x',-100,100,0),('y',-100,100,0)):
        number=value.get(name,default)
        if type(number)not in (int,float)or not math.isfinite(number)or not low<=number<=high:
            raise ValueError('TIFO 图片'+{'zoom':'缩放','x':'水平位置','y':'垂直位置'}[name]+'超出范围')
        result[name]=float(number)
    return result


def geometry(width,height,crop=None,out_width=1024,out_height=512):
    crop=normalize(crop)
    if min(width,height,out_width,out_height)<=0:raise ValueError('TIFO 图片尺寸无效')
    scale=(max if crop['fit']=='cover'else min)(out_width/width,out_height/height)*crop['zoom']
    w,h=width*scale,height*scale
    x=(out_width-w)/2+crop['x']/100*abs(out_width-w)/2
    y=(out_height-h)/2+crop['y']/100*abs(out_height-h)/2
    return {'x':x,'y':y,'width':w,'height':h,'scale':scale}


def render(image,crop=None,output_size=(1024,512)):
    from PIL import Image,ImageOps
    if not isinstance(output_size,(tuple,list))or len(output_size)!=2 or any(type(v)is not int or not 1<=v<=4096 for v in output_size):raise ValueError('TIFO 图片输出尺寸无效')
    output_size=tuple(output_size)
    crop=normalize(crop);image=image.convert('RGB')
    # Keep legacy default framing and its resampler byte-for-byte unchanged.
    if crop==DEFAULT:
        return ImageOps.fit(image,output_size,method=Image.Resampling.LANCZOS).convert('RGBA')
    box=geometry(image.width,image.height,crop,*output_size);inverse=1/box['scale']
    # Sample only the final canvas. Even an extreme panorama at 800% never
    # allocates an enlarged intermediate source image.
    return image.transform(output_size,Image.Transform.AFFINE,
        (inverse,0,-box['x']*inverse,0,inverse,-box['y']*inverse),
        resample=Image.Resampling.BICUBIC,fillcolor=(17,31,44)).convert('RGBA')


def fingerprint(asset,layout,crop=None):
    value=[REVISION,str(asset),str(layout),normalize(crop)]
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:20]
