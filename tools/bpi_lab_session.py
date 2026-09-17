#!/usr/bin/env python3
"""通用同次 UART／SSH 身分交接；測試公鑰安裝必須逐項取得明示授權。"""

from contextlib import closing
import base64
import copy
import hashlib
import ipaddress
from pathlib import Path
import re
import secrets
import shlex
import zlib

if __package__:
    from . import bpi_lab_deploy as deploy
    from . import bpi_lab_linux as linux
    from . import bpi_lab_station as station
else:
    import bpi_lab_deploy as deploy
    import bpi_lab_linux as linux
    import bpi_lab_station as station

require = deploy.require
BINDINGS = tuple(key for key in station.BINDINGS if key != "stage")

ROOT_UUID_CHECK = r'''
import json,os,re,stat,subprocess,sys
from pathlib import Path
wanted,nonce=sys.argv[1:]
matches=[]
for base in sorted(Path('/sys/class/block').iterdir()):
    if not re.fullmatch(r'mmcblk[0-9]+(?:p[1-9][0-9]*)?',base.name): continue
    fd=os.open('/dev/'+base.name,os.O_RDONLY|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        node=os.fstat(fd); number='%d:%d'%(os.major(node.st_rdev),os.minor(node.st_rdev))
        assert stat.S_ISBLK(node.st_mode) and number==(base/'dev').read_text().strip()
        result=subprocess.run(['/usr/sbin/blkid','-p','-s','UUID','-o','value','/proc/self/fd/'+str(fd)],
                              stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                              timeout=5,pass_fds=(fd,))
        assert result.returncode in (0,2) and len(result.stdout)<=4096 and len(result.stderr)<=4096
        if result.returncode==0 and result.stdout.decode().strip()==wanted:
            parent=base.resolve().parent if (base/'partition').exists() else base.resolve()
            matches.append({'name':base.name,'parent':str(parent),'devnum':number})
    finally: os.close(fd)
print(json.dumps({'nonce':nonce,'matches':matches}))
'''

ROOT_LABEL_CHECK = r'''
import json,os,stat,subprocess,sys,time
from pathlib import Path
wanted,label,nonce=sys.argv[1:]
end=time.monotonic()+45
def inventory():
    rows=[]
    for base in sorted(Path('/sys/class/block').iterdir()):
        size=int((base/'size').read_text())
        if not size: continue
        rows.append((base.name,(base/'dev').read_text().strip(),str(base.resolve(strict=True)),size))
    assert 0<len(rows)<=256
    return rows
before=inventory(); matches=[]; labels=[]
for name,number,resolved,size in before:
    assert time.monotonic()<end
    base=Path('/sys/class/block')/name
    fd=os.open('/dev/'+name,os.O_RDONLY|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        node=os.fstat(fd)
        assert stat.S_ISBLK(node.st_mode) and '%d:%d'%(os.major(node.st_rdev),os.minor(node.st_rdev))==number
        result=subprocess.run(['/usr/sbin/blkid','-p','-s','UUID','-s','LABEL','-o','export','/proc/self/fd/'+str(fd)],
                              stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                              timeout=min(5,max(0.001,end-time.monotonic())),pass_fds=(fd,))
        assert result.returncode in (0,2) and len(result.stdout)<=4096 and len(result.stderr)<=4096
        values={}
        for line in result.stdout.decode().splitlines():
            key,sep,value=line.partition('='); assert sep and key not in values
            values[key]=value
        parent=base.resolve().parent if (base/'partition').exists() else base.resolve()
        row={'name':name,'parent':str(parent),'devnum':number,'uuid':values.get('UUID'),'label':values.get('LABEL')}
        if values.get('UUID')==wanted: matches.append(row)
        if values.get('LABEL')==label: labels.append(row)
    finally: os.close(fd)
assert time.monotonic()<end and inventory()==before
print(json.dumps({'nonce':nonce,'matches':matches,'label_matches':labels,'inventory':before}))
'''

# 此固定程式僅讀取核心、掛載及公鑰；不讀 SSH 私鑰，不執行映像內腳本。
IDENTITY = r'''
import hashlib,json,os,re,stat,sys
from pathlib import Path
def read(p):
    with open(p,"rb") as f: b=f.read(65537)
    assert len(b)<=65536
    return b
def text(p): return read(p).decode().strip()
def num(d): return "%d:%d"%(os.major(d),os.minor(d))
before=num(os.stat("/").st_dev)
rows=[]
for line in text("/proc/self/mountinfo").splitlines():
    h,sep,t=line.partition(" - "); a=h.split(); b=t.split()
    assert sep and len(a)>=6 and len(b)>=3
    if a[4]=="/": rows.append((a,b))
assert len(rows)==1
a,b=rows[0]
assert a[2]==before and a[3]=="/"
root={"devnum":before,"fs":b[0],"mount_root":a[3],"sysfs":None,"parent":None,"parent_devnum":None,"uuid":None}
if not before.startswith("0:"):
    device=Path("/sys/dev/block")/before
    base=device.resolve(strict=True)
    assert text(base/"dev")==before
    parent=base.parent if (base/"partition").exists() else base
    root.update(sysfs=str(base),parent=str(parent),parent_devnum=text(parent/"dev"))
    matches=[]
    for p in Path("/dev/disk/by-uuid").iterdir():
        if p.resolve(strict=True)==Path("/dev")/base.name and num(p.stat().st_rdev)==before: matches.append(p.name)
    assert len(matches)==1
    root["uuid"]=matches[0]
media=[]
for p in sorted(Path("/sys/class/block").iterdir()):
    if not re.fullmatch(r"mmcblk[0-9]+",p.name): continue
    d=(p/"device").resolve(strict=True); controller=str(d).split("/mmc_host/")
    assert len(controller)==2 and not (p/"partition").exists()
    node=Path("/dev")/p.name; info=node.stat(follow_symlinks=False)
    assert stat.S_ISBLK(info.st_mode) and num(info.st_rdev)==text(p/"dev")
    media.append({"name":p.name,"sysfs":str(p.resolve(strict=True)),"devnum":text(p/"dev"),
                  "cid":text(p/"device/cid").lower(),"type":text(p/"device/type"),
                  "controller":controller[0],"bytes":int(text(p/"size"))*512})
rescue=None
if before.startswith("0:"):
    raw=read("/etc/bpi-rescue.json"); item=json.loads(raw)
    rescue={"schema":item["schema"],"kernel":item["kernel"],"identity_sha256":hashlib.sha256(raw).hexdigest()}
try: key=text(sys.argv[2])
except FileNotFoundError: key=None
assert num(os.stat("/").st_dev)==before
print(json.dumps({"nonce":sys.argv[1],"uid":os.geteuid(),"kernel":os.uname().release,
                  "machine":os.uname().machine,"boot_id":text("/proc/sys/kernel/random/boot_id"),
                  "dt_compatible":read("/sys/firmware/devicetree/base/compatible").rstrip(b"\0").decode().split("\0"),
                  "root":root,"media":media,"rescue":rescue,"host_key":key}))
'''


def identity_command(nonce, host_key_path="/etc/ssh/ssh_host_ed25519_key.pub"):
    require(type(nonce) is str and re.fullmatch(r"[0-9a-f]{64}", nonce), "採樣識別碼不符")
    data = base64.b64encode(zlib.compress(IDENTITY.encode(), 9)).decode()
    script = "import base64,zlib;exec(zlib.decompress(base64.b64decode(" + repr(data) + ")))"
    require(host_key_path in ("/etc/ssh/ssh_host_ed25519_key.pub", "/run/ssh/ssh_host_ed25519_key.pub"),
            "hostkey 路徑不在受限 SSH 公鑰範圍")
    return shlex.join(["python3", "-I", "-B", "-c", script, nonce, host_key_path])


def check_boot_root(console, expected, rescue_identity, deadline, *, root_binding=None):
    """首次登入可能先改帳戶；故須在 RAM 救援先排除原配根 UUID 指向受保護 SD。"""
    nonce = secrets.token_hex(32)
    label = None
    if root_binding is not None and root_binding.get("method") == "label":
        deploy.fields(root_binding, "method label uuid unique_in_image unique_on_hardware")
        label = root_binding["label"]
        require(type(label) is str and re.fullmatch(r"[A-Za-z0-9_.+-]{1,16}", label)
                and root_binding["uuid"] == expected["root"]["uuid"]
                and root_binding["unique_in_image"] is True and root_binding["unique_on_hardware"] is False,
                "LABEL 必須綁定映像內唯一名稱及實際根 UUID；不能預宣告實板唯一")
        packed = base64.b64encode(zlib.compress(ROOT_LABEL_CHECK.encode(), 9)).decode()
        program = "import base64,zlib;exec(zlib.decompress(base64.b64decode(" + repr(packed) + ")))"
        command = shlex.join(["python3", "-I", "-B", "-c", program, expected["root"]["uuid"], label, nonce])
    else:
        require(root_binding is None or root_binding == {"method": "uuid", "uuid": expected["root"]["uuid"]},
                "未知或錯配的根識別方法")
        command = shlex.join(["python3", "-I", "-B", "-c", ROOT_UUID_CHECK, expected["root"]["uuid"], nonce])
    require(len(command) < 3400, "根 UUID 唯讀程式超過 UART 命令界限")
    result = console.run_shell(command, timeout=deadline.remaining(60))
    require(result.exitcode == 0, "根 UUID 唯讀盤點失敗")
    record = station._json_loads(result.output)
    require(type(record) is dict and record.get("nonce") == nonce and type(record.get("matches")) is list
            and len(record["matches"]) == 1, "原配根 UUID 在實板不唯一；禁止引導及首次帳戶寫入")
    parents = [row["sysfs"] for row in rescue_identity["media"] if row["cid"] == expected["root"]["cid"]]
    require(len(parents) == 1 and record["matches"][0].get("parent") == parents[0],
            "原配根 UUID 不在已配對 eMMC；禁止寫入受保護媒體")
    if label is not None:
        require(type(record.get("inventory")) is list and 0 < len(record["inventory"]) <= 256
                and type(record.get("label_matches")) is list and len(record["label_matches"]) == 1
                and record["label_matches"][0] == record["matches"][0]
                and record["matches"][0].get("label") == label
                and record["matches"][0].get("uuid") == expected["root"]["uuid"],
                "LABEL 在可見媒體不唯一或未指向同一個 eMMC 根 UUID；禁止冷開機與首次寫入")
    return record


def public_key(value):
    require(type(value) is str and len(value) <= 4096, "SSH 公鑰格式不符")
    parts = value.split()
    require(2 <= len(parts) <= 3 and parts[0] == "ssh-ed25519", "只接受明示 Ed25519 hostkey")
    try:
        raw = base64.b64decode(parts[1], validate=True)
    except ValueError:
        raise deploy.core.DeployError("SSH 公鑰編碼不符") from None
    require(len(raw) == 51 and raw[:19] == b"\0\0\0\x0bssh-ed25519\0\0\0\x20", "SSH 公鑰結構不符")
    return " ".join(parts[:2])


def validate_identity(value, mode, contract, expected, nonce, *, allow_missing_host_key=False):
    deploy.fields(value, "nonce uid kernel machine boot_id dt_compatible root media rescue host_key")
    require(value["nonce"] == nonce and type(value["uid"]) is int and value["uid"] == 0,
            "本次 UART 採樣不是 root 或識別碼不符")
    require(type(value["boot_id"]) is str and re.fullmatch(
        r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", value["boot_id"]), "boot_id 無效")
    require(linux.ARCHITECTURES.get(value["machine"]) == expected["architecture"]
            and value["dt_compatible"] == expected["dt_compatible"], "UART 架構或 DT 身分不符")
    root, media = value["root"], value["media"]
    deploy.fields(root, "devnum fs mount_root sysfs parent parent_devnum uuid")
    require(type(media) is list and 1 <= len(media) <= 32, "UART 媒體清單不符")
    for entry in media:
        deploy.fields(entry, "name sysfs devnum cid type controller bytes")
        require(type(entry["name"]) is str and re.fullmatch(r"mmcblk[0-9]+", entry["name"])
                and type(entry["devnum"]) is str and re.fullmatch(r"[0-9]+:[0-9]+", entry["devnum"])
                and entry["type"] in ("MMC", "SD"), "UART 媒體不是唯一整碟")
        deploy.backup.validate_expected(entry["cid"], entry["bytes"], entry["controller"])
        require(type(entry["sysfs"]) is str and entry["sysfs"].startswith(entry["controller"] + "/mmc_host/")
                and entry["sysfs"].endswith("/block/" + entry["name"]), "UART 控制器與整碟路徑不符")
    require(all(len({row[key] for row in media}) == len(media) for key in ("cid", "devnum", "sysfs")),
            "UART 媒體身分重複")
    selected = {}
    for name, kind in (("expected", "MMC"), ("protected_sd", "SD")):
        matches = [entry for entry in media if all(entry.get(key) == item for key, item in contract[name].items())
                   and entry["type"] == kind]
        require(len(matches) == 1, "UART 未唯一核對配對 eMMC／受保護 SD")
        selected[name] = matches[0]
    require(root["mount_root"] == "/", "UART 根掛載不是直接根")
    if mode == "rescue":
        require(value["kernel"] == contract["rescue"]["kernel"] and value["rescue"] == contract["rescue"]
                and root["fs"] in ("rootfs", "ramfs", "tmpfs") and re.fullmatch(r"0:[0-9]+", root["devnum"])
                and all(root[key] is None for key in ("sysfs", "parent", "parent_devnum", "uuid")),
                "UART 未核對獨立 RAM 救援根與固定身分")
    else:
        require(mode == "customer" and value["kernel"] == expected["kernel_release"]
                and root["fs"] in ("ext2", "ext3", "ext4", "f2fs", "xfs")
                and root["parent"] == selected["expected"]["sysfs"]
                and root["parent_devnum"] == selected["expected"]["devnum"]
                and root["uuid"] == expected["root"]["uuid"]
                and type(root["sysfs"]) is str and (root["sysfs"] == root["parent"] or
                    (str(Path(root["sysfs"]).parent) == root["parent"] and re.fullmatch(
                        selected["expected"]["name"] + r"p[1-9][0-9]*", Path(root["sysfs"]).name))),
                "UART 核心、根 UUID 或 eMMC 父裝置不符")
    if not (allow_missing_host_key and value["host_key"] is None):
        public_key(value["host_key"])
    return value


def uart_identity(console, mode, contract, expected, deadline, nonce=None, *,
                  host_key_path="/etc/ssh/ssh_host_ed25519_key.pub", allow_missing_host_key=False):
    nonce = nonce or secrets.token_hex(32)
    result = console.run_shell(identity_command(nonce, host_key_path), timeout=deadline.remaining(45))
    require(result.exitcode == 0, "UART 固定身分程式未成功；不公開原始診斷")
    value = station._json_loads(result.output)
    deadline.remaining()
    return validate_identity(value, mode, contract, expected, nonce, allow_missing_host_key=allow_missing_host_key)


def ssh_identity(ssh, nonce, deadline, transport=None, *, host_key_path="/etc/ssh/ssh_host_ed25519_key.pub"):
    argv = deploy.backup.ssh_command(ssh["path"], "bpi-lab", {})
    argv[-1] = identity_command(nonce, host_key_path)
    buffers, code = {"stdout": bytearray(), "stderr": bytearray()}, None
    with closing((transport or deploy.backup.ssh_stream)(argv, deadline.end, deadline.clock)) as events:
        for kind, data in events:
            deadline.remaining()
            require(code is None, "SSH 退出後仍有事件")
            if kind == "exit":
                require(type(data) is int, "SSH 退出碼無效")
                code = data
            else:
                require(kind in buffers and type(data) is bytes and len(buffers[kind]) + len(data) <= 65536,
                        "SSH 身分回覆超界")
                buffers[kind].extend(data)
    require(code == 0, "同次 SSH 身分核對失敗")
    return station._json_loads(bytes(buffers["stdout"]))


def validate_setup(setup, authorized):
    deploy.fields(setup, "host_key_path install_key public_key authorized_keys peer_ipv4 wait_for")
    require(setup["host_key_path"] in ("/etc/ssh/ssh_host_ed25519_key.pub", "/run/ssh/ssh_host_ed25519_key.pub")
            and setup["authorized_keys"] in ("/root/.ssh/authorized_keys", "/etc/ssh/rescue_authorized_keys",
                                             "/run/ssh/authorized_keys")
            and setup["wait_for"] in ("existing", "armbian-firstrun") and type(setup["install_key"]) is bool,
            "SSH 安裝／就緒契約無效")
    peer = ipaddress.IPv4Address(setup["peer_ipv4"])
    require(not (peer.is_unspecified or peer.is_loopback or peer.is_multicast), "SSH 測試主機 IPv4 無效")
    if setup["install_key"]:
        require(authorized is True, "未明確授權新增測試 SSH 公鑰")
        public_key(deploy.checked_bytes(setup["public_key"], 4096).decode("ascii").strip())
    else:
        require(setup["public_key"] is None, "未安裝公鑰時不可附帶未使用的公鑰參照")


def install_key(console, setup, before, deadline):
    key = public_key(deploy.checked_bytes(setup["public_key"], 4096).decode("ascii").strip())
    line = ('from="' + setup["peer_ipv4"] + '",restrict ' + key + "\n").encode()
    # 只寫核定 authorized_keys；每層以 dir_fd 拒絕符號連結，並在寫入前重驗 boot_id／根裝置。
    script = ("import os,stat; from pathlib import Path; "
              "assert Path('/proc/sys/kernel/random/boot_id').read_text().strip()==" + repr(before["boot_id"]) + "; "
              "d=os.stat('/').st_dev; assert '%d:%d'%(os.major(d),os.minor(d))==" + repr(before["root"]["devnum"]) + "; "
              "p=" + repr(setup["authorized_keys"]) + "; "
              "root=os.open('/',os.O_RDONLY|os.O_DIRECTORY); "
              "parts=p.strip('/').split('/'); "
              "exec(\"for name in parts[:-1]:\\n"
              " try: os.mkdir(name,0o700,dir_fd=root)\\n"
              " except FileExistsError: pass\\n"
              " child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=root); os.close(root); root=child\\n\"); "
              "f=os.open(parts[-1],os.O_RDWR|os.O_APPEND|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK,0o600,dir_fd=root); "
              "s=os.fstat(f); assert stat.S_ISREG(s.st_mode) and s.st_nlink==1 and s.st_uid==0 and s.st_size<=65536; "
              "old=os.read(f,65537); assert len(old)<=65536 and (not old or old.endswith(b'\\n')); "
              "line=" + repr(line) + "; "
              "exec(\"if line not in old.splitlines(keepends=True):\\n"
              " assert os.write(f,line)==len(line)\\n\"); "
              "os.fchmod(f,0o600); os.fsync(f); os.close(f); os.fsync(root); os.close(root)")
    result = console.run_shell(shlex.join(["python3", "-I", "-B", "-c", script]), timeout=deadline.remaining(30))
    require(result.exitcode == 0, "測試公鑰安裝失敗；不公開原始診斷")


def establish(console, mode, contract, expected, ssh, output, deadline, request,
              *, previous_boot_id=None, transport=None, setup=None, key_authorized=False):
    """先核對 UART，再以其公鑰建立全新 SSH 設定，兩端 boot_id 必須相等。"""
    output = deploy.new_directory(output)
    host_path = setup["host_key_path"] if setup else "/etc/ssh/ssh_host_ed25519_key.pub"
    if setup is not None:
        validate_setup(setup, key_authorized)
    uart = uart_identity(console, mode, contract, expected, deadline, host_key_path=host_path,
                         allow_missing_host_key=setup is not None)
    require(previous_boot_id is None or uart["boot_id"] == previous_boot_id, "板子已重啟；不能沿用前階段或續作")
    if setup is not None:
        if setup["wait_for"] == "armbian-firstrun":
            require(mode == "customer", "救援不接受客戶首次啟動服務")
            for _ in range(90):
                ready = console.run_shell(
                    'case "$(systemctl show armbian-firstrun -p SubState --value)" in exited|dead) '
                    'systemctl is-active --quiet ssh;; *) false;; esac', timeout=deadline.remaining(10))
                if ready.exitcode == 0:
                    break
                deadline.pause(2)
            else:
                raise deploy.core.DeployError("首次啟動服務未在有界次數內就緒")
        if setup["install_key"]:
            install_key(console, setup, uart, deadline)
        fresh_identity = uart_identity(console, mode, contract, expected, deadline, uart["nonce"], host_key_path=host_path)
        require(all(fresh_identity[key] == value for key, value in uart.items() if key != "host_key"),
                "SSH 準備期間板子或根媒體身分改變")
        uart = fresh_identity
    key = public_key(uart["host_key"])
    fresh = copy.deepcopy(ssh)
    host = ssh["host"] if ssh["port"] == 22 else f"[{ssh['host']}]:{ssh['port']}"
    blob = (host + " " + key + "\n").encode("ascii")
    deploy.save(output, "uart-known-hosts", blob)
    fresh["known_hosts"] = {"path": str(output / "uart-known-hosts"), "sha256": hashlib.sha256(blob).hexdigest()}
    fixed = deploy.snapshot_ssh(fresh, output)
    remote = ssh_identity(fixed, uart["nonce"], deadline, transport, host_key_path=host_path)
    require(remote == uart, "UART 與 SSH 不是同次開機、根媒體或 hostkey")
    deploy.checked_bytes(fixed)
    record = {"schema": "bpi-lab-session-v1", "binding": {key: request[key] for key in BINDINGS},
              "mode": mode, "boot_id": uart["boot_id"], "identity": uart, "ssh": fresh,
              "uart_verified": True, "ssh_verified": True, "hostkey_source": "same-session-uart"}
    deploy.save(output, "session.json", deploy.encode(record))
    return record
