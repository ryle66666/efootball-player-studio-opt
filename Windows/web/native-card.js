// Original assets and native widget states; browser composition, not UE rendering.
import {formatCardDate,originalCardDate} from './card-design.js';
export function emblemKind(project){const id=project.identity?.team_id??project.native_card?.info?.team_id;return (id===undefined?!!project.native_card?.info?.assets?.club:Number(id)>0)?'club':'nation';}
// The blank preset's f04 can contain a fully opaque mechanical/background
// design. Native cards can also carry a mid-ground stage/logo graphic in f04
// (e.g. Show Time / Featured designs) that must sit behind the player portrait,
// while hollow border-only f04 frames stay in front of the portrait.
const f04BehindCache=new Map();
let probeCanvas=null,probeCtx=null;
function isF04Midground(layer){
  if(!layer||layer.slot!=='f04'||layer.kind==='video'||!layer.element)return false;
  const id=layer.asset_id;
  if(id&&f04BehindCache.has(id))return f04BehindCache.get(id);
  let behind=false;
  try{
    if(!probeCanvas){probeCanvas=document.createElement('canvas');probeCanvas.width=16;probeCanvas.height=22;probeCtx=probeCanvas.getContext('2d',{willReadFrequently:true});}
    probeCtx.clearRect(0,0,16,22);
    probeCtx.drawImage(layer.element,0,0,16,22);
    // Sample central torso box (x: 4..12, y: 5..16)
    const data=probeCtx.getImageData(4,5,8,11).data;
    let opaque=0,total=8*11;
    for(let i=3;i<data.length;i+=4)if(data[i]>128)opaque++;
    behind=(opaque/total)>0.12;
  }catch{behind=false;}
  if(id)f04BehindCache.set(id,behind);
  return behind;
}
export function cardLayerOrder(card,f04Behind=false){
  return (card.preset_id||f04Behind)
    ? {f01:0,f02:1,f04:2,f03:3,f05:4,f07:5,portrait:6,f06:7,f10:8,f09:9}
    : {f01:0,f02:1,portrait:2,f04:3,f03:4,f05:5,f06:6,f07:7,f10:8,f09:9};
}
export function cardFrontLayers(card,appearance={}){
  const dateOverride=appearance.card_date!==undefined||card?.preset_id||appearance.card_date_visible===false||appearance.hide_card_ui===true;
  const layers=appearance.card_background_override&&appearance.background_image
    ? [{slot:'f01',kind:'image',asset_id:appearance.background_image,blend:'source-over'}]
    : card.custom_motion?.large?.preview?.asset_id
    ? [{slot:'f02',kind:'video',asset_id:card.custom_motion.large.preview.asset_id,blend:'source-over'}]
    : card.layers.filter(layer=>!layer.slot.startsWith('b')&&!layer.slot.startsWith('m')&&layer.slot!=='portrait'&&!(dateOverride&&layer.slot==='f09')&&!(appearance.hide_card_ui&&layer.slot==='f06'));
  if(appearance.portrait&&!appearance.hide_portrait){
    const original=card.layers?.find(layer=>layer.slot==='portrait'&&layer.asset_id===appearance.portrait);
    if(!(card.baked_portrait&&original&&layers.some(layer=>layer.slot==='f01'))){
      layers.push({slot:'portrait',native_slot:original?.native_slot,kind:'image',asset_id:appearance.portrait,blend:'source-over'});
    }
  }
  const order=cardLayerOrder(card);return layers.sort((a,b)=>(order[a.native_slot??a.slot]??7)-(order[b.native_slot??b.slot]??7));
}
export function nativeCardState(project){
  const card=project.native_card,ui=card?.ui;if(!ui)return null;
  const source=project.source_player?.original_payload,original=source?.initial_booster_id_list||[];
  const slotCount=Math.max((project.booster_ids||[]).filter(Boolean).length,original[1]?2:original[0]?1:0),hasBooster=slotCount>0;
  let type=card.card_type==='BigTime'?5:ui.native_dispatch.kind_states[card.card_type]??0;
  let state=`PlayerType_${type}`;
  if(hasBooster&&ui.states[state+'_Booster'])state+='_Booster';
  else if(type===3&&card.native_agent>=340)state+='_BoosterNone';
  const tracks=ui.states[state]||{};
  const widgets=Object.fromEntries(Object.entries(ui.widgets).map(([key,value])=>[key,{...value,...tracks[key]}]));
  const slots=ui.states['BoosterSlot_'+(slotCount||'None')]||{};
  for(const key of ['ScaleBox_BoosterSlot_1','ScaleBox_BoosterSlot_2'])Object.assign(widgets[key],slots[key]||{});
  return {state,type,widgets,slotCount,hasBooster};
}
export class NativeCardRenderer {
  constructor(canvas,onStatus,catalog={}){
    this.canvas=canvas;this.onStatus=onStatus;this.catalog=catalog;
    this.media=new Map();this.fontFaces=new Map();this.generation=0;this.motion=true;
    this.key=null;this.resources=[];this.framesRendered=0;
  }
  async asset(layer){
    const id=layer.asset_id;
    if(this.media.has(id))return this.media.get(id).promise;
    const video=layer.kind==='video',element=video?document.createElement('video'):new Image();
    let settle;
    const promise=new Promise(resolve=>{
      settle=value=>{element.onloadeddata=element.onload=element.onerror=null;resolve(value);};
      if(video){
        element.muted=true;element.loop=true;element.playsInline=true;element.preload='auto';
        element.onloadeddata=()=>settle(element);
      }else element.onload=()=>settle(element);
      element.onerror=()=>settle(null);
    });
    this.media.set(id,{promise,release:()=>{
      settle(null);
      if(video)element.pause();
      element.removeAttribute('src');
      if(video)element.load();
    }});
    element.src='/assets/'+id;
    if(video)element.load();
    return promise;
  }
  async font(record){
    if(!record)return null;
    const id=record.asset_id;
    if(this.fontFaces.has(id))return this.fontFaces.get(id).promise;
    const family='Native'+id.slice(0,16);
    let face;
    try{face=new FontFace(family,'url(/assets/'+id+')');}catch{return null;}
    const entry={face};
    entry.promise=face.load().then(()=>{
      if(this.fontFaces.get(id)!==entry)return null;
      document.fonts.add(face);
      return family;
    },()=>null);
    this.fontFaces.set(id,entry);
    return entry.promise;
  }
  pruneAssets(media,fonts){
    for(const [id,entry] of this.media)if(!media.has(id)){entry.release();this.media.delete(id);}
    for(const [id,entry] of this.fontFaces)if(!fonts.has(id)){document.fonts.delete(entry.face);this.fontFaces.delete(id);}
  }
  stopPlayback(){
    this.generation++;cancelAnimationFrame(this.frame);
    for(const layer of this.resources)if(layer.kind==='video')layer.element?.pause();
    this.resources=[];this.decorations=null;this.fonts=null;
  }
  async update(project,simulation){
    this.project=project;this.simulation=simulation;
    const card=project.native_card,dateOverride=project.appearance.card_date!==undefined||card?.preset_id||project.appearance.card_date_visible===false;
    const key=JSON.stringify([card?.source_id,card?.preset_id,card?.custom_motion,card?.baked_portrait,card?.layers?.map(l=>[l.asset_id,l.blend,l.native_slot]),card?.fonts,card?.info,project.appearance.card_background_override,project.appearance.background_image,project.appearance.portrait,project.appearance.hide_portrait,project.appearance.hide_card_ui,Boolean(dateOverride)]);
    if(this.key===key){this.draw();return;}
    this.stopPlayback();this.key=key;const generation=this.generation;
    const frontLayers=cardFrontLayers(card,project.appearance),uiAssets=Object.entries(card.info?.assets||{});
    const fontRecords=['sans','headline','chinese'].map(k=>card.fonts[k]);
    this.pruneAssets(new Set([...frontLayers,...uiAssets.map(([,asset])=>asset)].map(asset=>asset.asset_id)),
      new Set(fontRecords.map(record=>record?.asset_id)));
    const [layers,decorations,[sans,headline,chinese]]=await Promise.all([
      Promise.all(frontLayers.map(async layer=>({...layer,element:await this.asset(layer)}))),
      Promise.all(uiAssets.map(async([name,asset])=>[name,await this.asset(asset)])),
      Promise.all(fontRecords.map(record=>this.font(record))),
    ]);
    if(generation!==this.generation)return;
    const f04Layer=layers.find(l=>l.slot==='f04');
    if(!card.preset_id&&isF04Midground(f04Layer)){
      const order=cardLayerOrder(card,true);
      layers.sort((a,b)=>(order[a.native_slot??a.slot]??7)-(order[b.native_slot??b.slot]??7));
    }
    this.resources=layers;this.decorations=Object.fromEntries(decorations);this.fonts={sans,headline,chinese};
    this.onStatus({layers:layers.filter(l=>l.element).length,videos:layers.filter(l=>l.element&&l.kind==='video').length,ui:decorations.filter(([,el])=>el).length,missing:layers.filter(l=>!l.element).length+(card.missing?.length||0)+(card.info?.missing?.length||0)});
    await Promise.all(layers.filter(l=>l.kind==='video'&&l.element).map(async layer=>{
      const video=layer.element;
      if(card.preview_only&&Number.isFinite(video.duration)&&video.duration>.5){await new Promise(resolve=>{video.addEventListener('seeked',resolve,{once:true});video.currentTime=Math.min(.8,video.duration/3);setTimeout(resolve,2000);});}
      else{video.currentTime=0;if(this.motion)video.play().catch(()=>{});}
    }));
    if(generation!==this.generation)return;
    this.draw();this.tick();
  }
  clear(){
    if(this.key===null)return;
    this.stopPlayback();this.pruneAssets(new Set(),new Set());
    this.key=null;this.project=null;this.simulation=null;
  }
  setMotion(value){this.motion=value;for(const layer of this.resources)if(layer.kind==='video'&&layer.element){if(value)layer.element.play().catch(()=>{});else layer.element.pause();}this.draw();this.tick();}
  tick(){cancelAnimationFrame(this.frame);if(!this.key||!this.motion||!this.resources.some(l=>l.kind==='video'&&l.element))return;this.frame=requestAnimationFrame(time=>{if(!document.hidden&&this.canvas.offsetParent&&(!this.lastFrame||time-this.lastFrame>=32)){this.draw();this.lastFrame=time;}this.tick();});}
  draw(){
    const p=this.project,card=p?.native_card;if(!card||!this.resources.length)return;
    const canvas=this.canvas,cw=card.preview_only?268:1200,ch=card.preview_only?384:1720;if(canvas.width!==cw||canvas.height!==ch){canvas.width=cw;canvas.height=ch;}
    const ctx=canvas.getContext('2d'),{width,height}=card.layout;
    ctx.setTransform(1,0,0,1,0,0);ctx.clearRect(0,0,canvas.width,canvas.height);ctx.save();ctx.scale(canvas.width/width,canvas.height/height);
    for(const layer of this.resources){
      const image=layer.element;if(!image||layer.kind==='video'&&image.readyState<2)continue;
      ctx.globalCompositeOperation=layer.slot==='f01'?'source-over':layer.blend||'source-over';
      if(layer.slot==='portrait'){
        const isSquareThumb=layer.native_slot!=='f07'&&image.width&&image.height&&(image.width/image.height)>=0.85;
        const zoom=p.appearance.photo_zoom||1,contain=isSquareThumb||p.appearance.portrait_fit==='contain'||card.preset_id&&p.native_art?.portrait?.kind!=='portrait';
        const scale=contain?Math.min(width/image.width,height/image.height):null,w=contain?image.width*scale:width,h=contain?image.height*scale:height;
        const x=(width-w*zoom)/2+(p.appearance.photo_x||0)*width/600,y=(isSquareThumb?(height-h*zoom)*0.48:height-h*zoom)+(p.appearance.photo_y||0)*height/860;
        ctx.drawImage(image,x,y,w*zoom,h*zoom);
      }else ctx.drawImage(image,0,0,width,height);
    }
    ctx.globalCompositeOperation='source-over';
    if(p.appearance.native_overlay_text!==false&&!p.appearance.hide_card_ui){
      const state=nativeCardState(p),widgets=state?.widgets;
      const transform=(box,draw)=>{
        if(!box||box.visible===false)return;ctx.save();const px=box.w*(box.pivot?.X??.5),py=box.h*(box.pivot?.Y??.5);
        ctx.translate(box.x+(box.Translation||0)+px,box.y+(box['Translation[1]']||0)+py);
        ctx.rotate((box.Rotation??box.angle??0)*Math.PI/180);ctx.scale(box.Scale??1,box['Scale[1]']??1);ctx.translate(-px,-py);draw();ctx.restore();
      };
      const text=(value,box,family)=>transform(box,()=>{
        if(!family||!value)return;ctx.fillStyle='#fff';ctx.strokeStyle='rgba(0,0,0,.3)';ctx.lineWidth=1;ctx.textAlign=box.align||'center';ctx.textBaseline='alphabetic';
        // Slate uses points at 96 DPI. Center the font line, not the glyph ink:
        // stencil digits have a different ascent/descent from the name font.
        const factor=family===this.fonts.chinese ? .888889 : 1;ctx.font=`${box.size*96/72*factor}px "${family}"`;
        const metrics=ctx.measureText(value),ascent=metrics.fontBoundingBoxAscent??metrics.actualBoundingBoxAscent,descent=metrics.fontBoundingBoxDescent??metrics.actualBoundingBoxDescent;
        const x=ctx.textAlign==='center'?box.w/2:0,y=(box.h+ascent-descent)/2;
        ctx.strokeText(value,x,y,box.w);ctx.fillText(value,x,y,box.w);
      });
      const image=(key,box)=>transform(box,()=>{const el=this.decorations?.[key];if(el)ctx.drawImage(el,0,0,box.w,box.h);});
      const name=p.display_name||p.identity.name||'',nameFont=/[^\u0000-\u024f]/.test(name)?this.fonts.chinese:this.fonts.sans;
      if(widgets){
        text(String(this.simulation.ovr),widgets.Text_Overall,this.fonts.headline);text(p.identity.position,widgets.Text_Position,this.fonts.sans);
        for(let i=0;i<6;i++)text(name,widgets[`Text_PlayerName_${i}`],nameFont);
        const customDate=p.appearance.card_date!==undefined||card.preset_id;
        const date=formatCardDate(p.appearance.card_date??(card.suppress_native_date?'':originalCardDate(card)));
        if(date&&p.appearance.card_date_visible!==false)text(date,{...widgets.Text_Date,visible:customDate?card.card_type==='BigTime':widgets.Text_Date.visible},this.fonts.sans);
        image('logo',widgets.Img_eFootballLogo);
        const info=card.info||{},club=emblemKind(p)==='club';
        image(club?'club':'nation',{...(club?widgets.EmblemClub||widgets.EmblemNationality:widgets.EmblemNationality),visible:true});
        const stars=widgets.ScaleBox_RarityStar;
        const rarity=p.appearance.card_rarity??info.rarity;if(rarity!=null)image('stars'+rarity,{...stars,x:stars.x-3,y:stars.y-3,w:117,h:27});
        for(let i=0;i<state.slotCount&&i<2;i++){
          const id=p.booster_ids?.[i]||0,booster=[...(p.custom_boosters||[]),...(this.catalog.boosters||[])].find(b=>b.id===id);
          const icon=!id?0:booster?.uuid?4:Math.min(5,Math.max(1,(booster?.category||0)+1));
          image('booster'+icon,widgets['ScaleBox_BoosterSlot_'+(i+1)]);
        }
        this.canvas.dataset.nativeLayout=state.state;
        this.canvas.dataset.nativeUiElements=JSON.stringify({stars:rarity,emblem:club?'club':'nation',boosters:state.slotCount,logo:true,name:state.type,date:customDate?date:null,portrait:!p.appearance.hide_portrait&&!!p.appearance.portrait});
      }else{
        text(String(this.simulation.ovr),card.layout.overall,this.fonts.headline);text(p.identity.position,card.layout.position,this.fonts.sans);text(name,card.layout.name,nameFont);
      }
    }else if(p.appearance.hide_card_ui){
      this.canvas.dataset.nativeUiElements=JSON.stringify({hidden:true,portrait:!p.appearance.hide_portrait&&!!p.appearance.portrait});
    }
    ctx.restore();this.canvas.dataset.nativeSource=card.source_id;
    this.canvas.dataset.nativeVideoFrames=JSON.stringify(this.resources.filter(l=>l.kind==='video'&&l.element).map(l=>({slot:l.slot,time:Number(l.element.currentTime.toFixed(3)),paused:l.element.paused,ready:l.element.readyState})));
    this.framesRendered++;
  }
}
