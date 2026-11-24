import {applyCardBackground} from './card-design.js';
const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

export async function openCardFaces({api,project,commit,render,toast}){
  const dialog=document.createElement('dialog');dialog.className='card-face-library';
  dialog.innerHTML='<div class="panel-title"><h2>我的卡面库</h2><button type="button" data-face-close>关闭</button></div><div data-face-rows>正在读取…</div>';
  document.body.append(dialog);dialog.showModal();
  dialog.addEventListener('close',()=>dialog.remove(),{once:true});
  async function refresh(){
    const {rows}=await api('card-faces',{});
    dialog.querySelector('[data-face-rows]').innerHTML=rows.map(row=>{
      const image=row.kind==='image'?row.image.asset_id:row.custom_motion.large.preview.asset_id;
      const preview=row.kind==='image'?`<img src="/assets/${escape(image)}" alt="${escape(row.label)}" loading="lazy">`:`<video src="/assets/${escape(image)}" muted loop controls preload="none"></video>`;
      return `<article class="panel"><div class="panel-body">${preview}<p>${escape(row.label)}</p><div class="button-row"><button type="button" data-face-use="${escape(row.id)}" ${row.available?'':'disabled'}>应用</button><button type="button" data-face-name="${escape(row.id)}">重命名</button><button type="button" data-face-remove="${escape(row.id)}">从卡面库删除</button></div></div></article>`;
    }).join('')||'<p class="note">导入的静态或动态背景会自动保存到这里。</p>';
  }
  dialog.addEventListener('click',async event=>{
    const button=event.target.closest('button');if(!button)return;
    try{
      if(button.hasAttribute('data-face-close')){dialog.close();return;}
      if(button.dataset.faceRemove){await api('card-faces/remove',{id:button.dataset.faceRemove});await refresh();return;}
      if(button.dataset.faceName){
        const label=window.prompt(t('卡面名称'));if(label===null)return;
        await api('card-faces/rename',{id:button.dataset.faceName,label});await refresh();return;
      }
      if(button.dataset.faceUse){
        const face=await api('card-faces/get',{id:button.dataset.faceUse});
        if(face.kind==='motion'&&!project.native_card)throw Error('请先选择原版卡面样式，再应用动态背景。');
        commit(()=>{
          if(face.kind==='image'){
            applyCardBackground(project,face.background_image);
          }else{project.native_card.custom_motion=structuredClone(face.custom_motion);project.native_card_disabled=false;project.appearance.card_background_override=false;}
        },'card-face');
        dialog.close();render();toast('已应用卡面背景。');
      }
    }catch(error){toast(error.message,true);}
  });
  try{await refresh();}catch(error){dialog.close();throw error;}
}
import {t} from './i18n.js';
