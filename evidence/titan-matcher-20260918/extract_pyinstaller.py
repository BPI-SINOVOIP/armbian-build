#!/usr/bin/env python3
"""只解出官方 PyInstaller 封裝，不執行入口程式。"""
import hashlib,json,pathlib,struct,sys,zlib
source=pathlib.Path(sys.argv[1]);out=pathlib.Path(sys.argv[2]);out.mkdir(parents=True,exist_ok=True)
b=source.read_bytes();cookie=b.rfind(b'MEI\x0c\x0b\x0a\x0b\x0e')
if cookie<0:raise ValueError('找不到 PyInstaller 封裝')
magic,size,toc_offset,toc_length,version,library=struct.unpack('!8sIIII64s',b[cookie:cookie+88]);base=cookie+88-size
pos=base+toc_offset;end=pos+toc_length;records=[]
while pos<end:
 length,offset,compressed,expanded,flag,kind=struct.unpack('!IIIIBc',b[pos:pos+18]);name=b[pos+18:pos+length].split(b'\0')[0].decode();p=pathlib.Path(name)
 if p.is_absolute() or '..' in p.parts:raise ValueError('封裝路徑不安全')
 data=b[base+offset:base+offset+compressed]
 if flag:data=zlib.decompress(data)
 assert len(data)==expanded
 if data:
  target=out/p;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
 records.append({'path':name,'type':kind.decode(),'size':len(data),'sha256':hashlib.sha256(data).hexdigest()});pos+=length
(out/'extraction.json').write_text(json.dumps({'source':str(source),'source_sha256':hashlib.sha256(b).hexdigest(),'python_version':version,'library':library.rstrip(b'\0').decode(),'files':records},indent=2)+'\n')
print(version,len(records));print('\n'.join(x['path'] for x in records if x['type'] in ['s','z']))
