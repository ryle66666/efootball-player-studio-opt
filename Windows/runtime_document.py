"""Load small startup settings and read tournament catalogues on demand."""
import copy
import json
import threading
from pathlib import Path

BASE='custom-resources/personal-player-studio/'
MANIFEST=BASE+'players.json'
REFERENCE_KEY='runtime_sections_v1'
SECTIONS={'tournament_standard_cards':BASE+'tournament-standard-cards.json',
          'tournament_emblems':BASE+'tournament-emblems.json'}
MAX_BYTES=256*1024*1024


def partition(document):
    """Return the startup document and separately installable JSON sections."""
    main=dict(document);references={};files={}
    for name,path in SECTIONS.items():
        if name not in main:continue
        value=main.pop(name)
        if not isinstance(value,dict):raise ValueError('Invalid tournament catalogue: '+name)
        raw=json.dumps(value,ensure_ascii=False,separators=(',',':')).encode('utf8')
        files[path]=raw;references[name]={'path':path,'bytes':len(raw)}
    if references:main[REFERENCE_KEY]=references
    else:main.pop(REFERENCE_KEY,None)
    return main,files


class Document(dict):
    def __init__(self,value,root):
        super().__init__(value)
        self._root=Path(root);self._lock=threading.RLock()
        self._references={}
        references=value.get(REFERENCE_KEY,{})
        if not isinstance(references,dict):raise ValueError('Invalid runtime catalogue references')
        for name,row in references.items():
            if name not in SECTIONS or not isinstance(row,dict)or row.get('path')!=SECTIONS[name]:
                raise ValueError('Invalid runtime catalogue path')
            size=row.get('bytes')
            if type(size)is not int or not 0<size<=MAX_BYTES:raise ValueError('Invalid runtime catalogue size')
            self._references[name]=row

    def __contains__(self,key):
        return dict.__contains__(self,key)or key in self._references

    def __getitem__(self,key):
        if dict.__contains__(self,key):return dict.__getitem__(self,key)
        if key not in self._references:raise KeyError(key)
        with self._lock:
            if dict.__contains__(self,key):return dict.__getitem__(self,key)
            row=self._references[key];path=self._root/row['path']
            if path.stat().st_size!=row['bytes']:raise ValueError('Runtime catalogue is incomplete: '+key)
            value=json.loads(path.read_text(encoding='utf8'))
            if not isinstance(value,dict):raise ValueError('Invalid runtime catalogue: '+key)
            dict.__setitem__(self,key,value)
            return value

    def get(self,key,default=None):
        try:return self[key]
        except KeyError:return default

    def __copy__(self):
        return Document(dict(self),self._root)

    def __deepcopy__(self,memo):
        result=Document({},self._root);memo[id(self)]=result
        for key,value in dict.items(self):dict.__setitem__(result,copy.deepcopy(key,memo),copy.deepcopy(value,memo))
        result._references=copy.deepcopy(self._references,memo)
        return result

    def copy(self):return self.__copy__()


def load(root):
    root=Path(root);path=root/MANIFEST
    if not path.is_file():raise RuntimeError('Personal-player manifest is missing')
    if path.stat().st_size>MAX_BYTES:raise ValueError('Personal-player manifest is too large')
    return Document(json.loads(path.read_text(encoding='utf8')),root)
