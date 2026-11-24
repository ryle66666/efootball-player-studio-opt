const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const artifactName=row=>row.name||row.path?.split(/[\\/]/).at(-1)||'';
export function resourceArtifacts(rows,platform='pc'){
 const statuses={needs_install:'需要安装',installed_match:'已安装且匹配，可复用',version_mismatch:'已安装，但内容版本不符',signature_mismatch:'签名不配套',reused:'文件未变化，可复用；安装状态待核对'};
 const hasContent=rows.some(row=>row.kind==='content_pack');
 const guidance=hasContent?(rows.every(row=>row.kind==='content_pack')?'保存内容包后，在文件管理器中选择使用对应游戏打开。':'先安装游戏程序 APK，再导入对应的 .73pack 内容包。首次打开程序时也可选择内容包。'):rows.some(row=>row.kind==='companion')?'先安装需要更新的配套资源，再安装游戏 APK。覆盖安装游戏时保留应用数据。':'安装游戏 APK；覆盖更新时保留应用数据。';
 return `<div class="completed-installs"><p>${guidance}</p>${rows.map(row=>{
  const title=row.kind==='content_pack'?'游戏内容包':row.kind==='companion'?'配套离线资源 APK':row.kind==='extension'?`扩展资源 APK ${row.dependency?.volume??''}`:hasContent?'游戏程序 APK':'游戏 APK';
  return `<div><strong>${esc(title)}</strong><p>${esc(row.kind==='content_pack'?'等待手动导入':statuses[row.status]||(row.reused?statuses.reused:statuses.needs_install))}</p>${row.dependency?`<small>${esc(row.dependency.package)} · 内容 ${(row.dependency.content_sha256||'').slice(0,12)}</small>`:''}<div class="welcome-actions">${row.url?`<a class="button secondary" href="${esc(row.url)}" download>保存文件</a>${row.kind==='content_pack'&&platform==='android'?'':`<button class="button primary" data-native-result="${esc(artifactName(row))}">${platform==='android'?'直接安装':'打开所在位置'}</button>`}`:`<span class="note">复用文件：${esc(row.path)}</span>`}</div></div>`;
 }).join('')}</div>`;
}

export function contentAutofillPackages(report){
 const rows=new Map();
 function visit(value){if(!value||typeof value!=='object')return;for(const row of Array.isArray(value.packages)?value.packages:[]){const key=row.package_id||row.id||JSON.stringify(row);rows.set(key,row);}visit(value.previous);visit(value.incremental);}
 visit(report);return [...rows.values()];
}
