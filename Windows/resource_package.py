"""Content-addressed resource-only APKs and atomic whole-set publication."""
import hashlib,json,os,struct,uuid,zipfile
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from android_manifest import Manifest,ANDROID,NONE
from apk_format import replace_entries,sign_v2,verify_v2,UINT32_LIMIT
from resource_planner import estimate
import resource_set as rs


def template_removals(template):
    with zipfile.ZipFile(template)as archive:
        names=archive.namelist()
        if 'assets/resource-bootstrap/resources.zip'not in names:raise ValueError('扩展资源需要已验证的原离线资源APK模板')
        Manifest(archive.read('AndroidManifest.xml'))
        return [n for n in names if n.startswith(('assets/','lib/','META-INF/'))or n.endswith('.dex')]


def template_size(template):
    return estimate(template,remove_entries=template_removals(template))['signed_upper_bound']+8192


def _resource_map(document,values):
    index=next((i for i,p in enumerate(document.chunks)if struct.unpack_from('<H',p)[0]==0x180),None)
    current=list(struct.unpack('<'+'I'*((len(document.chunks[index])-8)//4),document.chunks[index][8:]))if index is not None else[]
    current.extend([0]*max(0,max(values,default=-1)+1-len(current)))
    for key,value in values.items():current[key]=value
    raw=bytearray(struct.pack('<HHI',0x180,8,8+4*len(current))+struct.pack('<'+'I'*len(current),*current))
    if index is None:document.chunks.insert(document.pool+1,raw)
    else:document.chunks[index]=raw


def _start(document,name,attrs=()):
    part=bytearray(struct.pack('<HHIII',0x102,16,36+20*len(attrs),0,NONE)+struct.pack('<IIHHHHHH',NONE,document.index(name),20,20,len(attrs),0,0,0))
    for key,value,kind,resource in attrs:
        key_index=document.index(key);data=document.index(value)if kind==3 else value
        part.extend(struct.pack('<IIIHBBI',document.index(ANDROID),key_index,data if kind==3 else NONE,8,0,kind,data))
    return part


def _end(document,name):return bytearray(struct.pack('<HHIIIII',0x103,16,24,0,NONE,NONE,document.index(name)))


def _attribute_ids(document):
    part=next((p for p in document.chunks if struct.unpack_from('<H',p)[0]==0x180),None)
    return list(struct.unpack('<'+'I'*((len(part)-8)//4),part[8:]))if part is not None else[]


def _sort_application_attributes(document):
    # Android's typed-attribute lookup consumes resource-ID order. Appending
    # hasCode after allowBackup lets the platform silently use hasCode=true.
    ids=_attribute_ids(document)
    for node,tag,attrs in list(document.nodes()):
        if tag!='application':continue
        part=document.chunks[node];start,unit,count=struct.unpack_from('<HHH',part,24)
        prefix=bytearray(part[:16+start])
        def key(attr):
            index=struct.unpack_from('<I',part,attr['position']+4)[0]
            ident=ids[index]if index<len(ids)else 0
            return ident or 0x80000000
        ordered=sorted(attrs,key=key)
        for offset,name,namespace in((30,'id',ANDROID),(32,'class',None),(34,'style',None)):
            position=next((i+1 for i,a in enumerate(ordered)if a['name']==name and a['namespace']==namespace),0)
            struct.pack_into('<H',prefix,offset,position)
        document.chunks[node]=prefix+b''.join(part[a['position']:a['position']+unit]for a in ordered)


def _code_policy(raw):
    document=Manifest(raw);ids=_attribute_ids(document)
    applications=[(n,attrs)for n,tag,attrs in document.nodes()if tag=='application']
    if len(applications)!=1:raise ValueError('扩展APK必须只有一个应用声明')
    node,attrs=applications[0];part=document.chunks[node]
    order=[];code=[]
    for ordinal,attr in enumerate(attrs):
        index=struct.unpack_from('<I',part,attr['position']+4)[0]
        ident=ids[index]if index<len(ids)else 0;order.append(ident or 0x80000000+ordinal)
        if attr['name']=='hasCode':code.append((attr,ident))
        if attr['name']in('name','appComponentFactory','backupAgent','manageSpaceActivity','zygotePreloadName'):
            raise ValueError('扩展APK残留应用代码入口')
    if order!=sorted(order):raise ValueError('扩展APK应用属性未按资源编号排序，安卓会误读代码声明')
    if len(code)!=1 or code[0][1]!=0x0101000c or code[0][0]['namespace']!=ANDROID or code[0][0]['type']!=18 or code[0][0]['data']!=0:
        raise ValueError('扩展APK缺少可由安卓识别的 hasCode=false 声明')
    return {'has_code':False,'application_attributes_resource_id_sorted':True}


def verify_code_policy(apk):
    """Also guard inherited caches, not only newly built extension packages."""
    with zipfile.ZipFile(apk)as archive:
        if any(n.endswith('.dex')for n in archive.namelist()):raise ValueError('纯资源扩展APK不应包含DEX')
        return _code_policy(archive.read('AndroidManifest.xml'))


def _manifest(raw,dependency):
    doc=Manifest(raw);original=doc.info()['package'];doc.clone(dependency['package'],'工坊扩展资源 '+str(dependency['volume']),{original:dependency['package']})
    # The extension contains no executable application, providers or original
    # resource installer. Its only entry is a harmless framework Activity for
    # explicit package discovery; Android package/code identity stays valid.
    chunks=[];inside=False;depth=0
    for part in doc.chunks:
        kind=struct.unpack_from('<H',part)[0]
        if kind in(0x102,0x103):tag=doc.string(struct.unpack_from('<I',part,20)[0])
        else:tag=None
        if inside:
            if kind==0x102:depth+=1
            elif kind==0x103:
                if depth==0 and tag=='application':chunks.append(part);inside=False;continue
                depth-=1
            continue
        chunks.append(part)
        if kind==0x102 and tag=='application':inside=True;depth=0
    if inside:raise ValueError('资源模板application结构无效')
    doc.chunks=chunks
    ids={}
    for number,tag,attrs in list(doc.nodes()):
        if tag=='manifest':
            version=next((a for a in attrs if a['name']=='versionCode'),None)
            if version is None:raise ValueError('资源模板缺少原生versionCode字段')
            struct.pack_into('<HBBI',doc.chunks[number],version['position']+12,8,0,16,dependency['version_code'])
            name=next((a for a in attrs if a['name']=='versionName'),None)
            if name:doc.set_string(number,name,'资源-'+dependency['content_sha256'][:16])
        if tag=='application':
            part=doc.chunks[number];start,unit,count=struct.unpack_from('<HHH',part,24)
            keep=[a for a in attrs if a['name']not in('name','hasCode','appComponentFactory','backupAgent','manageSpaceActivity','zygotePreloadName')]
            prefix=bytearray(part[:16+start]);records=[bytes(part[a['position']:a['position']+unit])for a in keep]
            index=doc.index('hasCode');ids[index]=0x0101000c
            records.append(struct.pack('<IIIHBBI',doc.index(ANDROID),index,NONE,8,0,18,0))
            struct.pack_into('<H',prefix,28,len(records));struct.pack_into('<I',prefix,4,len(prefix)+len(records)*unit)
            doc.chunks[number]=prefix+b''.join(records)
    name_id=doc.index('name');export_id=doc.index('exported');ids.update({name_id:0x01010003,export_id:0x01010010})
    _resource_map(doc,ids)
    entries=[_start(doc,'activity',[('name','android.app.Activity',3,0x01010003),('exported',1,18,0x01010010)]),
             _start(doc,'intent-filter'),_start(doc,'action',[('name',rs.DISCOVERY_ACTION,3,0x01010003)]),
             _end(doc,'action'),_end(doc,'intent-filter'),_end(doc,'activity')]
    at=next(i for i,p in enumerate(doc.chunks)if struct.unpack_from('<H',p)[0]==0x103 and doc.string(struct.unpack_from('<I',p,20)[0])=='application')
    doc.chunks[at:at]=entries
    _sort_application_attributes(doc)
    result=doc.bytes();_code_policy(result);check=Manifest(result).info()
    if check['package']!=dependency['package']or check['version_code']!=dependency['version_code']or check['authorities']:
        raise ValueError('扩展资源模板身份改写核验失败')
    return result


def build_volume(template,volume,output,key,certificate,*,limit=UINT32_LIMIT):
    output=Path(output);dep=volume['dependency'];files=volume['files'];payloads=dict(volume['payloads'])
    if dep.get('role')!='extension'or rs.volume_content(files)!=dep['content_sha256']:raise ValueError('扩展卷内容与规划摘要不同')
    from export_payload import metadata
    if set(payloads)!={r['entry']for r in files}:raise ValueError('扩展资源负载集合与规划不一致')
    for row in files:
        if metadata(payloads[row['entry']])!={k:row[k]for k in('bytes','sha256')}:raise ValueError('扩展资源实际负载与映射摘要不同：'+row['path'])
    signer=hashlib.sha256(certificate.public_bytes(serialization.Encoding.DER)).hexdigest()
    if dep['signers']!=[signer]:raise ValueError('扩展资源必须与规划/游戏使用同一本地签名')
    if output.exists():raise ValueError('扩展APK输出路径已存在')
    with zipfile.ZipFile(template)as archive:manifest=_manifest(archive.read('AndroidManifest.xml'),dep)
    payloads.update({'AndroidManifest.xml':manifest,rs.VOLUME_MANIFEST:rs.canonical(dict(protocol=rs.PROTOCOL,package=dep))})
    remove=[n for n in template_removals(template)if n not in payloads]
    measured=estimate(template,payloads,remove_entries=remove)
    if measured['signed_upper_bound']>=limit:raise ValueError('扩展APK实际封装预检超限：'+dep['package'])
    unsigned=output.with_name('.'+output.name+'.'+uuid.uuid4().hex+'.unsigned')
    signed=output.with_name('.'+output.name+'.'+uuid.uuid4().hex+'.signed');reserved=False
    try:
        prepared=replace_entries(template,unsigned,payloads,remove_entries=remove,store_ucas=True,prepare_signing=True)
        sign_v2(unsigned,signed,key,certificate,prepared=prepared,consume=True)
        signature=verify_v2(signed)
        if signed.stat().st_size>=limit:raise ValueError('签名后扩展APK超出单包容量：'+dep['package'])
        from apk_installation import identity
        actual=identity(signed)
        if actual['package']!=dep['package']or actual['version_code']!=dep['version_code']or actual['signers']!=[signer]:raise ValueError('签名后扩展APK身份不一致')
        code_policy=verify_code_policy(signed)
        from export_payload import verify_replacements
        with zipfile.ZipFile(signed)as archive:
            verified=verify_replacements(archive,payloads)
            if any(n in archive.namelist()for n in remove):raise ValueError('扩展APK残留旧模板资源')
        with output.open('xb'):reserved=True
        os.replace(signed,output)
        return dict(path=str(output),dependency=dep,size=output.stat().st_size,signature=signature,
                    verified_entries=len(verified['digests']),measured=measured,code_policy=code_policy)
    except BaseException:
        if reserved:output.unlink(missing_ok=True)
        raise
    finally:unsigned.unlink(missing_ok=True);signed.unlink(missing_ok=True)


def atomic_publish(staging,destination,*,verify,limit=UINT32_LIMIT,verified_apks=None):
    """Publish verified APKs and content files with one directory rename."""
    # Windows app-container ACLs can deny GetFinalPathNameByHandle(strict)
    # for our freshly created directory while ordinary stat/read succeeds.
    # Resolve the available parents, then require the actual directory below.
    staging=Path(staging).resolve();destination=Path(destination).resolve()
    if not staging.is_dir()or destination.exists()or staging==destination or staging in destination.parents:raise ValueError('整套资源发布路径无效或已存在')
    destination.parent.mkdir(parents=True,exist_ok=True)
    if staging.stat().st_dev!=destination.parent.stat().st_dev:raise ValueError('整套APK必须在同一卷暂存后原子发布')
    apks=sorted(staging.glob('*.apk'))
    contents=sorted(staging.glob('*.73pack'))
    if not apks:raise ValueError('整套资源暂存目录没有APK')
    for path in apks:
        if path.stat().st_size>=limit:raise ValueError('签名成品超出单APK容量：'+path.name)
        stamp=(verified_apks or{}).get(str(path))
        if stamp is None:verify_v2(path)
        else:
            from export_payload import identity as file_identity
            if file_identity(path.stat())!=stamp:raise ValueError('已核对的 APK 在发布前发生变化：'+path.name)
    validation=verify()
    if not isinstance(validation,dict)or validation.get('verified')is not True:raise ValueError('整套资源未通过跨包读回验证')
    report=dict(protocol=rs.PROTOCOL,complete=True,validation=validation,
                files=[dict(name=p.name,bytes=p.stat().st_size)for p in [*apks,*contents]])
    with(staging/'complete.json').open('xb')as handle:handle.write(rs.canonical(report));handle.flush();os.fsync(handle.fileno())
    # Both resolved endpoints above were checked; no user save directory or
    # existing export is replaced. One same-volume rename is the sole commit.
    if destination.exists():raise ValueError('发布目标在核验期间出现，未覆盖已有成品')
    os.rename(staging,destination)
    return dict(directory=str(destination),files=[str(destination/p.name)for p in [*apks,*contents]],complete=True)
