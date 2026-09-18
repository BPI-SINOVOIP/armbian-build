"""掛載官方 PYZ 位元碼供診斷；不啟動 GUI、USB 或刷機入口。"""
import os,sys,marshal,_struct,zlib,_frozen_importlib
bundle_root=os.environ['TITAN_EXTRACT_ROOT']
pyz_name='PYZ.pyz' if os.path.exists(bundle_root+'/PYZ.pyz') else 'PYZ-00.pyz'
pyz_data=open(bundle_root+'/'+pyz_name,'rb').read()
pyz_offset=_struct.unpack('!I',pyz_data[8:12])[0]
pyz_toc=dict(marshal.loads(pyz_data[pyz_offset:]))
class BundleFinder:
 def find_spec(self,fullname,path=None,target=None):
  if fullname == 'struct':
   return _frozen_importlib.ModuleSpec(fullname,self,is_package=False)
  if fullname in pyz_toc:
   return _frozen_importlib.ModuleSpec(fullname,self,is_package=bool(pyz_toc[fullname][0]))
 def create_module(self,spec):return None
 def exec_module(self,module):
  if module.__name__ == 'struct':
   exec(marshal.loads(open(bundle_root+'/struct','rb').read()),module.__dict__)
   return
  kind,start,length=pyz_toc[module.__name__]
  module.__file__=bundle_root+'/'+module.__name__.replace('.','/')+'.py'
  exec(marshal.loads(zlib.decompress(pyz_data[start:start+length])),module.__dict__)
sys.meta_path.insert(0,BundleFinder())
script=os.environ['TITAN_DIAGNOSTIC_SCRIPT']
exec(compile(open(script).read(),script,'exec'))
