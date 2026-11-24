import {artifactName} from './resource-export-ui.js';

const key=result=>result?.path||result?.name||result?.url||'';
const rank=row=>row.kind==='companion'?0:row.kind==='extension'?1:2;

export function installableArtifacts(result,earlier=[]){
  const rows=new Map();
  const add=row=>{
    if(!row||typeof row!=='object')return;
    const name=artifactName(row);
    if(!name||!name.toLowerCase().endsWith('.apk'))return;
    if(row.status==='installed_match'||(row.reused&&!row.url))return;
    rows.set(name,{...row,name});
  };
  earlier.forEach(add);
  (Array.isArray(result?.artifacts)?result.artifacts:[result]).forEach(add);
  return [...rows.values()].sort((a,b)=>rank(a)-rank(b)||(a.dependency?.volume||0)-(b.dependency?.volume||0));
}

export class ExportInstallation{
  constructor({api,platform,hasQueued=()=>false}){
    Object.assign(this,{api,platform,hasQueued});
    this.running=false;this.earlier=[];this.plans=new Map();
  }
  observe(path,data){
    if(!/^native-apk\/(start|status)$/.test(path)||!data)return;
    if(data.phase==='working'){
      if(!this.running)this.earlier=[];
      this.running=true;return;
    }
    if(data.phase==='error'){this.running=false;this.earlier=[];return;}
    if(data.phase!=='done'||!data.result||!this.running)return;
    const result=data.result;
    if(this.hasQueued()){
      this.earlier.push(...(Array.isArray(result.artifacts)?result.artifacts:[result]));return;
    }
    this.plans.set(key(result),installableArtifacts(result,this.earlier));
    while(this.plans.size>20)this.plans.delete(this.plans.keys().next().value);
    this.running=false;this.earlier=[];
  }
  async open(result){
    if(!result||!artifactName(result))throw Error('尚未找到生成的文件');
    if(this.platform!=='android'||result.kind==='content_pack'||artifactName(result).toLowerCase().endsWith('.73pack')){
      return this.api('exports/open',{name:artifactName(result)});
    }
    const rows=this.plans.get(key(result))||installableArtifacts(result);
    if(!rows.length)throw Error('没有需要安装的 APK');
    return this.api('exports/install',{names:rows.map(row=>row.name)});
  }
}
