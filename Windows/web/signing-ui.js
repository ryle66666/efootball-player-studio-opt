export async function openSigning({api,toast}){
  const state=await api('signing/status',{}),dialog=document.createElement('dialog');
  dialog.innerHTML=`<div class="panel-title"><h2>工坊签名身份</h2><button type="button" data-sign-close>关闭</button></div><div class="panel-body"><p>${state.configured?'已配置签名身份，可导出给另一端工坊使用。':'首次应用时自动创建签名身份；已有游戏请导入之前的身份。'}</p><label class="field"><span>身份文件密码</span><input type="password" minlength="8" autocomplete="new-password" data-sign-password></label><div class="button-row"><button type="button" data-sign-import>导入身份</button><button type="button" data-sign-export ${state.configured?'':'disabled'}>导出身份</button></div><div data-sign-result></div></div>`;
  document.body.append(dialog);dialog.showModal();dialog.addEventListener('close',()=>dialog.remove(),{once:true});
  dialog.addEventListener('click',async event=>{
    const button=event.target.closest('button');if(!button)return;
    if(button.hasAttribute('data-sign-close')){dialog.close();return;}
    const password=dialog.querySelector('[data-sign-password]').value;
    try{
      if(password.length<8)throw Error('身份文件密码需要至少8个字符。');
      if(button.hasAttribute('data-sign-import')){
        const selected=await api('dialog',{category:'signing'});if(!selected.path)return;
        await api('signing/import',{path:selected.path,password});dialog.close();toast('已导入签名身份。');
      }else if(button.hasAttribute('data-sign-export')){
        const result=await api('signing/export',{password});
        const link=document.createElement('a');link.href=result.url;link.download=result.name;link.textContent='保存签名身份文件';link.className='button secondary';
        dialog.querySelector('[data-sign-result]').replaceChildren(link);
      }
    }catch(error){toast(error.message,true);}
  });
}
