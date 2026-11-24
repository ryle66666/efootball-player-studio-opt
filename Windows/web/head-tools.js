import * as THREE from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {TransformControls} from 'three/addons/controls/TransformControls.js';

export class HeadTools{
  static async load(viewer,spec){
    const tools=new HeadTools(viewer,spec),loader=new GLTFLoader();
    const actualLoad=loader.loadAsync(spec.export_url);
    const rawLoad=spec.raw_url===spec.export_url?actualLoad:loader.loadAsync(spec.raw_url);
    // The reference is rendered as a wireframe. Skip its glTF texture decoding.
    const referenceLoad=spec.reference_url?new GLTFLoader().register(()=>({
      name:'StudioReferenceMaterial',loadMaterial:()=>Promise.resolve(new THREE.MeshBasicMaterial())
    })).loadAsync(spec.reference_url):null;
    const [actual,raw,reference]=await Promise.all([actualLoad,rawLoad,referenceLoad]);
    tools.actual=actual.scene;tools.raw=raw.scene;tools.reference=reference?.scene||new THREE.Group();
    const roots=[['export',tools.actual]];
    if(tools.raw!==tools.actual)roots.push(['raw',tools.raw]);
    roots.push(['reference',tools.reference]);
    for(const [role,root] of roots){
      root.name='HeadPreview-'+role;root.userData.previewOnly=role!=='export';tools.root.add(root);
      root.traverse(n=>{if(n.isSkinnedMesh)tools.base.set(n.uuid,n.skeleton.bones.map((b,i)=>({bone:b,quaternion:b.quaternion.clone(),axis:new THREE.Vector3(1,0,0).transformDirection(n.skeleton.boneInverses[i])})));
        if(n.isMesh&&role==='reference'){n.userData.referenceMesh=true;n.material=new THREE.MeshBasicMaterial({color:0x80dcca,wireframe:true,transparent:true,opacity:.32,depthWrite:false});n.renderOrder=2;}
      });
    }
    if(tools.raw!==tools.actual)tools.raw.visible=false;
    tools.reference.visible=false;
    for(const [key,position] of Object.entries(spec.anchors)){
      const marker=new THREE.Mesh(new THREE.SphereGeometry(.007,16,12),new THREE.MeshBasicMaterial({color:key==='head'?0xffce72:0x80ddc5,depthTest:false}));
      marker.position.fromArray(position);marker.name=key==='head'?'头部定位点':'颈根定位点';marker.renderOrder=20;marker.userData.previewOnly=true;marker.userData.handle=key;tools.markers.set(key,marker);tools.root.add(marker);
    }
    return tools;
  }
  constructor(viewer,spec){this.viewer=viewer;this.spec=spec;this.root=new THREE.Group();this.root.name='ExportEquivalentHead';this.markers=new Map();this.base=new Map();this.test={jaw:0,blink:0,raw:false,reference:false,handles:false,selected:'head'};}
  activate(){
    const v=this.viewer;this.control=new TransformControls(v.camera,v.renderer.domElement);this.control.setMode('translate');this.control.setSize(.7);this.control.setTranslationSnap(.001);
    this.gizmo=this.control.getHelper();v.scene.add(this.gizmo);this.gizmo.visible=false;
    this.control.addEventListener('dragging-changed',e=>{v.controls.enabled=!e.value;});
    this.control.addEventListener('objectChange',()=>{
      const key=this.test.selected,marker=this.markers.get(key);if(!marker)return;
      const offset=marker.position.clone().sub(new THREE.Vector3().fromArray(this.spec.anchors[key])).multiplyScalar(100);
      const values=offset.toArray().map(x=>Math.round(THREE.MathUtils.clamp(x,-10,10)*100)/100);
      // The head marker includes the existing vertical-placement slider.
      if(key==='head')values[1]=Math.round(THREE.MathUtils.clamp(values[1]-(Number(this.project?.model.head_vertical_cm)||0),-10,10)*100)/100;
      v.onBindingChange?.(key,values);
    });
    this.pointer=e=>{
      if(!this.test.handles||this.control.dragging)return;
      const rect=v.renderer.domElement.getBoundingClientRect(),pointer=new THREE.Vector2((e.clientX-rect.left)/rect.width*2-1,-(e.clientY-rect.top)/rect.height*2+1),ray=new THREE.Raycaster();ray.setFromCamera(pointer,v.camera);
      const hit=ray.intersectObjects([...this.markers.values()])[0];if(hit){this.setTest({selected:hit.object.userData.handle});v.onHeadSelection?.(this.test.selected);}
    };
    v.renderer.domElement.addEventListener('pointerdown',this.pointer);this.setProject(v.project);
    const values={};document.querySelectorAll('[data-head-option],[data-head-test]').forEach(el=>{const key=el.dataset.headOption||el.dataset.headTest;values[key]=el.type==='checkbox'?el.checked:el.tagName==='SELECT'?el.value:Number(el.value);});this.setTest(values);
  }
  dispose(){this.viewer.controls.enabled=true;this.viewer.renderer.domElement.removeEventListener('pointerdown',this.pointer);this.control?.detach();if(this.gizmo)this.viewer.scene.remove(this.gizmo);this.control?.dispose();}
  setProject(project){this.project=project;const m=project.model,handles=m.binding_handles||{};
    const roots=this.raw===this.actual?[this.actual]:[this.actual,this.raw];
    for(const [key,label] of [['head','Head'],['neck_root','Root']]){
      const values=[...(handles[key]||[0,0,0])];if(key==='head')values[1]+=Number(m.head_vertical_cm)||0;
      for(const root of roots)root.traverse(mesh=>{if(!mesh.morphTargetDictionary)return;for(let i=0;i<3;i++){const index=mesh.morphTargetDictionary['Studio'+label+'XYZ'[i]];if(index!==undefined)mesh.morphTargetInfluences[index]=values[i];}});
      if(this.markers.has(key))this.markers.get(key).position.fromArray(this.spec.anchors[key]).add(new THREE.Vector3().fromArray(values).multiplyScalar(.01));
    }
    this.applyTest();
  }
  setTest(values){Object.assign(this.test,values);this.testing=this.test.jaw>0||this.test.blink>0;
    const showRaw=this.test.raw||this.testing;
    this.actual.visible=this.raw===this.actual||!showRaw;
    if(this.raw!==this.actual)this.raw.visible=showRaw;
    this.reference.visible=this.test.reference;
    for(const marker of this.markers.values())marker.visible=this.test.handles;
    if(this.control){this.control.detach();if(this.test.handles&&this.markers.has(this.test.selected))this.control.attach(this.markers.get(this.test.selected));this.gizmo.visible=this.test.handles&&this.markers.size>0;}
    this.applyTest();
  }
  applyTest(){
    for(const poses of this.base.values())for(const {bone,quaternion,axis}of poses){bone.quaternion.copy(quaternion);
      const angle=bone.name==='skf_jaw'?this.test.jaw*28:bone.name.startsWith('skf_eyelid_t_')?this.test.blink*18:bone.name.startsWith('skf_eyelid_b_')?this.test.blink*-8:0;
      if(angle)bone.quaternion.multiply(new THREE.Quaternion().setFromAxisAngle(axis,THREE.MathUtils.degToRad(angle)));
    }
    this.root.updateMatrixWorld(true);this.root.traverse(mesh=>{if(mesh.isSkinnedMesh)mesh.skeleton.update();});
  }
}
