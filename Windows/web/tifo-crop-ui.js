const defaults={fit:'cover',zoom:1,x:0,y:0};
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

export function normalizeTifoCrop(value=null){
 if(value===null||value===undefined)return {...defaults};
 if(typeof value!=='object'||Array.isArray(value)||Object.keys(value).some(k=>!Object.prototype.hasOwnProperty.call(defaults,k)))throw Error('TIFO 图片调整格式无效');
 const out={...defaults,...value};
 if(!['cover','contain'].includes(out.fit))throw Error('TIFO 图片适配方式无效');
 for(const [key,min,max]of [['zoom',.1,8],['x',-100,100],['y',-100,100]]){
  if(typeof out[key]!=='number'||!Number.isFinite(out[key])||out[key]<min||out[key]>max)throw Error('TIFO 图片调整超出范围');
 }
 return out;
}

export function tifoCropGeometry(width,height,crop=null,outWidth=1024,outHeight=512){
 crop=normalizeTifoCrop(crop);
 if(![width,height,outWidth,outHeight].every(n=>Number.isFinite(n)&&n>0))throw Error('TIFO 图片尺寸无效');
 const scale=(crop.fit==='cover'?Math.max:Math.min)(outWidth/width,outHeight/height)*crop.zoom;
 const w=width*scale,h=height*scale;
 return {x:(outWidth-w)/2+crop.x/100*Math.abs(outWidth-w)/2,
  y:(outHeight-h)/2+crop.y/100*Math.abs(outHeight-h)/2,width:w,height:h,scale};
}

export function tifoCropControls(object,prefix='',options={}){
 const c=normalizeTifoCrop(object.image_crop);
 const index=options.objectIndex,objectMode=Number.isInteger(index),scope=objectMode?'object-'+index:'stand';
 const key=prefix+(objectMode?'.objects.'+index:'.stand_tifo'),slot=prefix.match(/^stadium\.slots\.(\d+)$/)?.[1];
 const attributes=`data-world-tifo-scope="${scope}"${objectMode?` data-world-tifo-object="${index}"`:''}${slot!==undefined?` data-world-tifo-slot="${slot}"`:''}`;
 const canvas=objectMode&&Array.isArray(object.image_canvas)?object.image_canvas:[1024,512];
 const [w,h]=canvas.every(v=>Number.isInteger(v)&&v>0&&v<=4096)&&canvas.length===2?canvas:[1024,512];
 const slider=(name,label,min,max,step,value,suffix)=>`<label class="world-tifo-slider"><span>${label}</span><input type="range" data-world-tifo-crop="${name}" ${attributes} ${prefix?`data-world-field="${esc(key+'.image_crop.'+name)}"`:''} aria-label="${objectMode?'展示板':'TIFO'} 图片${label}" min="${min}" max="${max}" step="${step}" value="${value}"><output ${attributes} data-world-tifo-value="${name}">${esc(suffix)}</output></label>`;
 return `<div class="world-tifo-image-editor" ${attributes}><h4>图片画面调整</h4><canvas width="${w}" height="${h}" style="aspect-ratio:${w}/${h}" ${attributes} data-world-tifo-crop-canvas role="img" aria-label="${objectMode?'展示板':'TIFO'} 图片裁切预览"></canvas><div class="portrait-actions"><button type="button" class="button secondary" ${attributes} data-world-tifo-fit="contain">完整显示</button><button type="button" class="button secondary" ${attributes} data-world-tifo-fit="cover">填满并居中</button></div>${slider('zoom','缩放',.1,8,.05,c.zoom,Math.round(c.zoom*100)+'%')}${slider('x','左右位置',-100,100,1,c.x,Math.round(c.x)+'%')}${slider('y','上下位置',-100,100,1,c.y,Math.round(c.y)+'%')}<p class="note">调整图片内容，不移动游戏里的${objectMode?'展示板':'海报'}。缩小时可留下深色边；原图保留，可随时重调。松开滑块后更新三维预览，再保存球场与赛事。</p></div>`;
}

export function paintTifoCrop(canvas,source,crop=null,ImageType=globalThis.Image){
 if(!canvas?.getContext||typeof ImageType!=='function')return;
 canvas._tifoCrop=normalizeTifoCrop(crop);
 const draw=()=>{
  const img=canvas._tifoImage;if(!img?.naturalWidth||!img?.naturalHeight)return;
  const ctx=canvas.getContext('2d');if(!ctx)return;
  const b=tifoCropGeometry(img.naturalWidth,img.naturalHeight,canvas._tifoCrop,canvas.width,canvas.height);
  ctx.fillStyle='#111f2c';ctx.fillRect(0,0,canvas.width,canvas.height);
  ctx.drawImage(img,b.x,b.y,b.width,b.height);
 };
 if(canvas._tifoSource===source){draw();return;}
 canvas._tifoSource=source;const image=new ImageType();canvas._tifoImage=image;
 image.onload=()=>{if(canvas._tifoImage===image&&canvas._tifoSource===source)draw();};
 image.onerror=()=>{if(canvas._tifoImage!==image)return;canvas.getContext('2d')?.clearRect(0,0,canvas.width,canvas.height);};
 image.src=source;
 if(image.complete)draw();
}
