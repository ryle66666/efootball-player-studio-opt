const CURRENT='studio-custom-skills-v2-2';
const LEGACY='studio-custom-skills-v2-1';
const DEFAULT_NATIVE_TO_EDITOR={75:74,76:75,77:76,78:77};

// Native additional-slot IDs and editor design IDs occupy different domains.
// Metadata from the native catalogue (_studio_skill_ids) is already editor IDs.
export function importedSkills(payload,catalog){
 const protocol=payload._studio_custom_skill_protocol;
 const mapping=catalog.CUSTOM_SKILL_NATIVE_TO_EDITOR||DEFAULT_NATIVE_TO_EDITOR;
 const nativeAdditional=payload.additional_skill_id||[];
 const nativeInnate=payload._studio_innate_design;
 const nativeRows=[...nativeAdditional,...(nativeInnate||[])];
 const ambiguous=protocol!==CURRENT&&protocol!==LEGACY&&nativeRows.some(id=>Number.isInteger(id)&&id>=75&&id<=78);
 const issue=ambiguous?{code:'unknown_custom_skill_protocol',protocol:protocol??null,
  message:'来源扩展技能编号协议不明，原始编号已保留。请从原工程重新导入或使用带协议说明的来源包；当前球员禁止重新编译，避免技能错位。',
  additional_skill_id:structuredClone(nativeAdditional),innate_design:structuredClone(nativeInnate??null)}:null;
 const decode=values=>values.filter(Number.isInteger).flatMap(id=>{
  if(protocol===CURRENT){if(id===74)return [];return [mapping[id]??id];}
  if(protocol===LEGACY)return [id];
  if(id===74||id>=75&&id<=78)return [];
  return [id];
 });
 const visible=values=>[...new Set(values.map(id=>[51,68].includes(id)?52:id))].filter(id=>!catalog.SKILL_LABELS[id]?.includes('预留'));
 const additional=decode(nativeAdditional),customAdditional=new Set(additional.filter(id=>(catalog.CUSTOM_SKILL_IDS||[]).includes(id)));
 const inferred=payload._studio_skill_ids||Object.entries(catalog.PARAMETER_BY_SKILL).filter(([,index])=>payload.parameters?.[index]===1).map(([id])=>Number(id));
 const innate=visible(Array.isArray(nativeInnate)?decode(nativeInnate):inferred.filter(id=>!customAdditional.has(id)));
 return {innate,additional:visible(additional).filter(id=>!innate.includes(id)),issue};
}
