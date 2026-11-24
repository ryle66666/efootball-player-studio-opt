// Generated from native_overall.py by maintenance/build_native_overall_ui.py.
// Android ARM64 integer kernel and native lineup preprocessing; no regression.
export const NATIVE_OVERALL = {"revision":"android-native-ovr-integer-v2","parameters":[47,14,18,17,19,21,22,20,27,28,23,15,24,25,37,34,38,35,36,39,16,32,29,30,31,40,33,26],"weights":[[186,136,49,49,61,37,12,12,37,49,49,62,99],[0,14,61,61,61,98,98,98,171,159,159,173,210],[13,27,86,86,122,171,171,171,196,159,159,210,123],[0,14,61,61,37,98,110,122,122,159,159,123,62],[0,0,37,37,24,49,73,61,73,86,86,86,37],[27,41,61,61,122,208,135,135,196,73,73,99,37],[40,68,147,147,122,159,196,196,159,98,98,74,12],[0,27,24,24,37,73,86,86,184,159,159,284,358],[0,14,24,24,12,12,24,24,12,12,12,12,12],[0,14,24,24,12,12,24,24,12,12,12,12,12],[0,55,24,24,61,24,12,12,24,24,24,25,62],[13,286,147,147,220,86,49,49,24,12,12,0,0],[0,191,86,86,122,86,24,24,24,12,12,12,12],[0,82,37,37,98,37,12,12,12,12,12,12,12],[53,27,24,24,49,73,24,24,73,61,61,99,123],[13,136,220,220,61,61,196,196,98,220,220,86,99],[40,150,184,184,61,86,159,159,86,159,159,99,123],[80,204,98,98,122,49,24,24,24,37,37,37,86],[0,0,24,24,12,24,61,61,24,73,73,74,86],[133,109,37,37,37,12,12,12,12,24,24,37,62],[279,0,0,0,0,0,0,0,0,0,0,0,0],[226,0,0,0,0,0,0,0,0,0,0,0,0],[226,0,0,0,0,0,0,0,0,0,0,0,0],[173,0,0,0,0,0,0,0,0,0,0,0,0],[173,0,0,0,0,0,0,0,0,0,0,0,0],[0,68,196,196,196,196,147,147,86,49,49,49,37],[4,4,4,4,4,4,4,4,4,4,4,4,4],[0,14,24,24,24,24,24,24,24,24,24,12,12]],"positionColumns":[0,1,3,2,4,5,7,6,8,10,9,11,12],"abilities":[14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,34,35,36,37,38,39,40],"aptitudeFactors":{"14":[0.8199999928474426,0.9399999976158142,1.0],"18":[0.800000011920929,0.9200000166893005,1.0],"17":[0.800000011920929,0.9200000166893005,1.0],"19":[1.0,1.0,1.0],"21":[0.800000011920929,0.9200000166893005,1.0],"22":[0.800000011920929,0.9200000166893005,1.0],"20":[0.800000011920929,0.9200000166893005,1.0],"27":[0.800000011920929,0.9200000166893005,1.0],"28":[0.800000011920929,0.9200000166893005,1.0],"23":[0.800000011920929,0.9200000166893005,1.0],"15":[0.8199999928474426,0.9399999976158142,1.0],"24":[0.800000011920929,0.9200000166893005,1.0],"25":[1.0,1.0,1.0],"37":[0.8199999928474426,0.9399999976158142,1.0],"34":[0.800000011920929,0.9200000166893005,1.0],"38":[0.8199999928474426,0.9399999976158142,1.0],"35":[0.8399999737739563,0.9599999785423279,1.0],"36":[0.8399999737739563,0.9599999785423279,1.0],"39":[0.800000011920929,0.9200000166893005,1.0],"16":[0.8199999928474426,0.9399999976158142,1.0],"32":[0.8199999928474426,0.9399999976158142,1.0],"29":[0.8199999928474426,0.9399999976158142,1.0],"30":[0.8199999928474426,0.9399999976158142,1.0],"31":[0.8199999928474426,0.9399999976158142,1.0],"40":[0.8399999737739563,0.9599999785423279,1.0],"26":[1.0,1.0,1.0]}};
export const RATING_VERSION=NATIVE_OVERALL.revision;
const clamp=(value,low,high)=>Math.max(low,Math.min(high,value));

export function nativeWeightedScore(parameters,position=parameters[0],boosts={}){
  if(parameters.length!==147||!Number.isInteger(position)||position<0||position>=13)throw Error('原生总评参数或位置无效');
  const column=NATIVE_OVERALL.positionColumns[position];
  const values=NATIVE_OVERALL.parameters.map(index=>parameters[index]);
  if(values.some(value=>!Number.isInteger(value)||value<0||value>255))throw Error('原生总评输入需要是整数');
  values[0]-=111;values[26]=Math.floor(values[26]*59/3)+40;
  return values.reduce((sum,value,i)=>sum+NATIVE_OVERALL.weights[i][column]*clamp(Math.max(0,value-25)+(boosts[NATIVE_OVERALL.parameters[i]]||0),15,95),0);
}

export function nativeRating(parameters,position=parameters[0],boosts={}){
  return Math.max(40,Math.floor((nativeWeightedScore(parameters,position,boosts)+500)/1000));
}

export function ratingParameters(project,catalog,stats=project.base_abilities){
  const source=project.source_player?.original_payload?.parameters||[],identity=project.identity||{};
  const parameters=Array.from({length:147},(_,i)=>source[i]??0);
  parameters[0]=catalog.POSITIONS.indexOf(identity.position||'CF');
  parameters[47]=Math.trunc(identity.height_cm??source[47]??180);
  const weak=project.traits?.weak_foot_accuracy;
  parameters[33]=weak===undefined?(source[33]??1):['低','普通','高','极高'].indexOf(weak);
  for(const [index,value]of Object.entries(stats))if(NATIVE_OVERALL.abilities.includes(Number(index)))parameters[Number(index)]=Math.trunc(value);
  return parameters;
}

export function nativeDelta(value,multiplier){
  const f=Math.fround;return Math.trunc(f(f(f(value)*multiplier)-f(value)));
}

export function positionAptitude(project,position){
  return project.position_aptitudes?.[position]??(position===project.identity.position?2:0);
}

export function nativeLineupParameters(parameters,multiplier=1,aptitude=2,manager=false){
  if(!Number.isInteger(aptitude)||aptitude<0||aptitude>2)throw Error('原生位置适应度无效');
  const result=[...parameters];if(!manager&&aptitude===2)return result;
  for(const index of NATIVE_OVERALL.abilities){
    let value=result[index];
    if(aptitude<2)value+=nativeDelta(value,NATIVE_OVERALL.aptitudeFactors[index][aptitude]);
    if(manager)value+=nativeDelta(value,multiplier);
    result[index]=clamp(value,40,99);
  }
  return result;
}

export function nativeLineupRating(parameters,position,boosts={},multiplier=1,aptitude=2,manager=false){
  return nativeRating(nativeLineupParameters(parameters,multiplier,aptitude,manager),position,boosts);
}
