#!/usr/bin/python3
"""在隔離的一般映像副本驗證套件安裝、來源身分與移除；不接觸實體媒體。"""
from pathlib import Path
import hashlib,json,os,shutil,stat,subprocess
base=Path('/media/pi/SMCI/bpi/f3-cm6-connectivity-20260922')
work=base/'installation-validation-rc2'
work.mkdir(exist_ok=False)
image=work/'rootfs.ext4'
source=Path('/media/pi/SMCI/bpi/f3-cm6-vendor-20260918-rc2/output/bpi-cm6-emmc/payload/rootfs.ext4')
record=json.loads((base/'bluetooth-package-rc2/package-manifest.json').read_text())
package=base/'bluetooth-package-rc2'/record['artifact']
assert hashlib.sha256(package.read_bytes()).hexdigest()==record['sha256']
mount=work/'mount';mount.mkdir()
attached=[]
def run(argv,codes=(0,)):
    result=subprocess.run(list(map(str,argv)),capture_output=True,text=True,env={**os.environ,'LC_ALL':'C.UTF-8','DEBIAN_FRONTEND':'noninteractive'})
    print(json.dumps({'argv':list(map(str,argv)),'exit_code':result.returncode,'stdout':result.stdout,'stderr':result.stderr},ensure_ascii=False),flush=True)
    if result.returncode not in codes: raise RuntimeError('驗證命令失敗')
    return result
run(['cp','--reflink=auto','--sparse=always',source,image])
try:
    run(['mount','-o','loop,nodev,nosuid',image,mount]);attached.append(mount)
    for name in ('dev','proc','sys','run'):
        target=mount/name;target.mkdir(exist_ok=True)
        run(['mount','-t','tmpfs','-o','nosuid,mode=755','tmpfs',target]);attached.append(target)
    for name,major,minor in (('null',1,3),('zero',1,5),('random',1,8),('urandom',1,9)):
        os.mknod(mount/'dev'/name,stat.S_IFCHR|0o666,os.makedev(major,minor))
    policy=mount/'usr/sbin/policy-rc.d'
    policy.write_text('#!/bin/sh\n# 離線驗證期間禁止啟動服務。\nexit 101\n');policy.chmod(0o755)
    target=mount/'var/tmp'/package.name;shutil.copyfile(package,target)
    run(['chroot',mount,'dpkg','-i','/var/tmp/'+package.name])
    status=run(['chroot',mount,'dpkg-query','-W','-f=${Status}\t${Version}\n','bpi-cm6-bluetooth'])
    assert 'install ok installed' in status.stdout
    for relative,item in record['files'].items():
        if relative.startswith('DEBIAN/'):continue
        path=mount/relative
        assert path.is_file() and path.stat().st_size==item['bytes']
        assert hashlib.sha256(path.read_bytes()).hexdigest()==item['sha256']
    run(['systemctl','--root='+str(mount),'is-enabled','bpi-cm6-bluetooth.service'])
    run(['systemd-analyze','--root='+str(mount),'verify','bpi-cm6-bluetooth.service'])
    run(['chroot',mount,'/usr/lib/bpi-cm6-bluetooth/rtk_hciattach','-l'])
    run(['chroot',mount,'python3','-m','py_compile','/usr/sbin/bpi-cm6-bluetooth'])
    run(['chroot',mount,'dpkg','--remove','bpi-cm6-bluetooth'])
    run(['chroot',mount,'dpkg','-i','/var/tmp/'+package.name])
    run(['systemctl','--root='+str(mount),'is-enabled','bpi-cm6-bluetooth.service'])
    run(['chroot',mount,'dpkg','-i','/var/tmp/'+package.name])
    run(['systemctl','--root='+str(mount),'is-enabled','bpi-cm6-bluetooth.service'])
    run(['systemctl','--root='+str(mount),'disable','bpi-cm6-bluetooth.service'])
    run(['chroot',mount,'dpkg','-i','/var/tmp/'+package.name])
    disabled=run(['systemctl','--root='+str(mount),'is-enabled','bpi-cm6-bluetooth.service'],codes=(1,))
    assert disabled.stdout.strip()=='disabled'
    run(['chroot',mount,'dpkg','--purge','bpi-cm6-bluetooth'])
    for relative in record['files']:
        if not relative.startswith('DEBIAN/'):
            assert not (mount/relative).exists(), '移除後仍有套件檔案'
    assert not (mount/'etc/systemd/system/multi-user.target.wants/bpi-cm6-bluetooth.service').is_symlink()
    result={'status':'passed','scope':'isolated-rc2-rootfs-install-remove-reinstall-upgrade-disable-preservation-and-purge','package_sha256':record['sha256'],'source_rootfs':str(source),'hardware_validation':'pending','host_sysfs_and_devices_exposed':False}
    (work/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
finally:
    for target in reversed(attached): run(['umount',target])
print('隔離副本安裝與移除驗證通過。',flush=True)
