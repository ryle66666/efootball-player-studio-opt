export const skinToneColors=["#a98675", "#a2775d", "#a27455", "#87644d", "#654a3c", "#553e34"];
export const skinToneLabels=['1 · 很浅肤色','2 · 浅肤色','3 · 中等肤色','4 · 棕肤色','5 · 深棕肤色','6 · 很深肤色'];
export const physique=[['neck_length','颈长'],['neck_size','颈围'],['shoulder_height','肩高'],['shoulder_width','肩宽'],['chest','胸围'],['waist','腰围'],['arm_size','上臂围'],['arm_length','臂长'],['thigh_size','大腿围'],['calf_size','小腿围'],['leg_length','腿长'],['head_height','头部长度'],['head_width','头部宽度'],['head_depth','头部厚度']];
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const defaultGloves=[{id:1,name:'explorer grip'},{id:101,name:'Predator'},{id:201,name:'Vapor Grip3'},{id:301,name:'Future One Grip 1 NC'},{id:311,name:'ULTRA Ultimate Hybrid'},{id:601,name:'Attrakt Fusion Guardian'},{id:701,name:'Wrap Aqua Control'},{id:901,name:'Konstriktor Elite NG Hazard'}];
const eqSelect=(key,label,cur,options)=>`<label class="field"><span>${label}</span><select data-equipment="${key}">${options.map(([value,text])=>`<option value="${value}" ${(cur||'keep')===value?'selected':''}>${text}</option>`).join('')}</select></label>`;
const wristChoices=[['keep','跟随模板球员'],['off','不佩戴'],['white','白色绷带'],['team','球衣队色'],['black','黑色绷带'],['blue','浅蓝绷带']];
export function equipmentPanel(project,catalog={}){
  const e=project.equipment||{},boots=catalog.boots||[],gloves=(catalog.gloves&&catalog.gloves.length)?catalog.gloves:defaultGloves;
  const isGK=String(project.identity?.position||'').toUpperCase()==='GK';
  const gloveSelect=isGK
    ? `<select data-equipment="gloves_id"><option value="" ${e.gloves_id==null?'selected':''}>跟随模板球员</option>${e.gloves_id!=null&&!gloves.some(g=>g.id===e.gloves_id)?`<option selected value="${e.gloves_id}">当前型号 #${e.gloves_id}</option>`:''}${gloves.map(g=>`<option value="${g.id}" ${e.gloves_id===g.id?'selected':''}>${esc(g.name)} · #${g.id}</option>`).join('')}</select>`
    : `<select data-equipment="gloves_id" disabled><option value="" selected>仅门将（GK）位置可选</option></select><small>仅当主要位置为门将（GK）时可选择手套。</small>`;
  return `<section class="panel"><div class="panel-title"><h2>球衣样式与比赛装备</h2><span class="tag">每名球员独立保存</span></div><div class="panel-body"><div class="field-grid"><label class="field"><span>球鞋型号</span><select data-equipment="boots_id"><option value="" ${e.boots_id==null?'selected':''}>跟随模板球员</option>${e.boots_id!=null&&!boots.some(b=>b.id===e.boots_id)?`<option selected value="${e.boots_id}">当前型号 #${e.boots_id}</option>`:''}${boots.map(b=>`<option value="${b.id}" ${e.boots_id===b.id?'selected':''}>${esc(b.name)}${b.color?' · '+esc(b.color):''} · #${b.id}</option>`).join('')}</select></label><label class="field"><span>守门员手套</span>${gloveSelect}</label>${eqSelect('sleeves','球衣样式 / 袖长',e.sleeves,[['keep','跟随模板球员'],['seasonal','随季节'],['short','短袖'],['long','长袖']])}${eqSelect('socks','球袜长度',e.socks,[['keep','跟随模板球员'],['standard','标准长度'],['long','长袜'],['short','短袜']])}${eqSelect('shirt_tail','球衣下摆穿法',e.shirt_tail,[['keep','跟随模板球员'],['tucked','扎进裤子'],['untucked','放出来']])}${eqSelect('tights','紧身打底裤',e.tights,[['keep','跟随模板球员'],['off','无'],['short','短款紧身裤'],['long','长款紧身裤']])}${eqSelect('inner_shirt','紧身内衣',e.inner_shirt,[['keep','跟随模板球员'],['off','无'],['standard','常规领'],['highneck','高领']])}${eqSelect('wrist_tape_left','左手护腕 / 绷带',e.wrist_tape_left,wristChoices)}${eqSelect('wrist_tape_right','右手护腕 / 绷带',e.wrist_tape_right,wristChoices)}${eqSelect('ankle_taping','脚踝绷带',e.ankle_taping,[['keep','跟随模板球员'],['off','不佩戴'],['on','佩戴']])}${eqSelect('player_gloves','冬季外场手套',e.player_gloves,[['keep','跟随模板球员'],['off','不佩戴'],['on','冬季佩戴']])}</div><p class="note">所有装备与护具默认跟随模板球员，未改动的位段逐字节保留；护腕、下摆、紧身衣裤、脚踝绷带与冬季手套同时支持完整 APK 与 .73pack 小包导出。头模窗口只展示头部，装备效果请在游戏中查看。</p></div></section>`;
}
export function bodyPanel(project,range){
  const m=project.model||{};
  const srcId=project.source_player?.id?String(project.source_player.id):'';
  const curId=m.appearance_source_id?String(m.appearance_source_id):'';
  const curName=m.appearance_source_name||(curId&&curId===srcId?(project.source_player?.name||''):'');
  const isOverride=Boolean(curId&&srcId&&curId!==srcId);
  const templateBox=`<div class="appearance-template-box" style="margin:0 0 18px;padding:14px 16px;border:1px solid var(--line);border-radius:var(--r);background:var(--surface-2)">
    <div style="display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px">
      <strong>外观与身体配置模板</strong>
      <span class="tag">${curId?`${curName?esc(curName)+' · ':''}#${esc(curId)}`:'跟随原球员'}</span>
    </div>
    <p class="note" style="margin:0 0 10px">用于控制身体底层外观与手臂纹身来源。选错带纹身底板时，可搜一名不带纹身的原版球员直接替换；<strong>已调好的 14 项体型、肤色、脸模、卡面与动作手感全部保留</strong>。</p>
    <div class="input-row">
      <input class="input" id="appearance-template-query" type="search" placeholder="输入原版球员名或编号搜索（如 Messi / 编号）">
      <button type="button" class="button secondary" data-appearance-search>搜索模板</button>
      ${isOverride?`<button type="button" class="button subtle" data-appearance-reset>恢复跟随原球员</button>`:''}
    </div>
    <div id="appearance-template-results" style="margin-top:10px"></div>
  </div>`;
  return `<section class="panel"><div class="panel-title"><h2>游戏原生体型与肤色</h2></div><div class="panel-body">${templateBox}<p class="note">0–14 是游戏参数档位。保持身高时，腿长与躯干比例联动；头部三项分别控制长、宽、厚。复制球员时保留其原生体型、肤色和纹身；更换卡面或导入自制头模时继续沿用。当前窗口仅展示头部，完整身体效果以游戏内为准。</p>${m.physique?`<div class="field-grid">${physique.map(([k,n])=>range('model.physique.'+k,n,0,14)).join('')}${range('model.torso_length','躯干比例（与腿长联动）',0,14)}</div><label class="field"><span>原生肤色档位（1–6，从浅到深）</span><select data-bind="model.skin_tone" data-number="1">${Array.from({length:6},(_,i)=>`<option value="${i+1}" ${m.skin_tone===i+1?'selected':''}>${skinToneLabels[i]}</option>`).join('')}</select><div class="skin-tone-swatches" role="group" aria-label="六档原生肤色参考">${skinToneLabels.map((label,i)=>`<button type="button" class="skin-tone-swatch" data-skin-tone="${i+1}" aria-pressed="${m.skin_tone===i+1}" title="${label}"><i style="background:${skinToneColors[i]}"></i><span>${label}</span></button>`).join('')}</div><small>色块取自原包 T_Sk1–6_Skin_D 漫反射贴图中央区域的中位色，仅供无光照参考；不是游戏最终肤色。六档均可选择；下拉框灰底表示当前选中项。深浅名称为方便识别的说明，面部贴图与游戏光照也会影响观感。</small></label>`:'<p>正在读取原版体型参数…</p>'}${range('identity.height_cm','身高（与资料页同步）',140,220,1,' cm')}${range('model.head_vertical_cm','头模垂直位置（厘米）',-10,10,.5,' cm')}<label class="switch-label"><input type="checkbox" data-bind="model.neck_seam" ${m.neck_seam!==false?'checked':''}>对截断颈部自动接合；完整颈胸模型保持原形</label>${m.asset&&!m.native_source?.path?'<p class="note">自制头模按上方的独立表情开关实装；关闭时使用导入的面部权重。</p>':''}<p class="note">参数随工程保存，并写入 APK 的外观表和脸模 SkinColor。脸部贴图仍使用所选原图，不会自动重绘贴图中的肤色。</p></div></section>`;
}
