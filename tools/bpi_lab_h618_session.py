"""0845 同次 UART 登入與嚴格 SSH 綁定；憑證只取自明示的私有檔案。"""

import base64
from contextlib import closing
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shlex

if __package__:
    from . import bpi_lab_h618_lifecycle as life
    from . import bpi_lab_linux as linux
    from . import bpi_lab_network as network
else:
    import bpi_lab_h618_lifecycle as life
    import bpi_lab_linux as linux
    import bpi_lab_network as network

safe, require = life.safe, life.require
BINDINGS = ("work_key", "attempt_id", "station_id", "hardware_id", "image_sha256",
            "boot_config_sha256", "test_version", "mode")


def binding(request):
    return {key: request[key] for key in BINDINGS}


def private_file(path, maximum=16384):
    path = life._path(path)
    with safe.open_root(path.parent) as directory, safe.open_file(directory, path.name) as stream:
        before = os.fstat(stream.fileno())
        require(before.st_uid == os.geteuid() and before.st_mode & 0o077 == 0
                and before.st_nlink == 1 and 0 < before.st_size <= maximum,
                "憑證檔案必須私有、單一連結且有界")
        blob = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
        require(len(blob) == before.st_size and (before.st_mtime_ns, before.st_ctime_ns) ==
                (after.st_mtime_ns, after.st_ctime_ns), "憑證讀取期間變動")
    return blob


def public_key(blob):
    require(type(blob) is bytes and len(blob) <= 4096
            and b"\n" not in blob.strip() and b"\r" not in blob.strip(), "公鑰須為有界單行")
    fields = blob.strip().split()
    require(len(fields) >= 2 and fields[0] == b"ssh-ed25519", "只接受 Ed25519 公鑰")
    try:
        raw = base64.b64decode(fields[1], validate=True)
    except ValueError:
        raise ValueError("公鑰編碼無效") from None
    require(len(raw) == 51 and raw[:19] == b"\0\0\0\x0bssh-ed25519\0\0\0\x20", "公鑰結構無效")
    return b" ".join(fields[:2]).decode("ascii")


def _dt_compatible(config, mode):
    require(mode in ("rescue", "customer"), "Linux 身分模式無效")
    expected = config["dt_compatible"]
    life._fields(expected, ("rescue", "customer"))
    compatible = expected[mode]
    require(type(compatible) is list and compatible and len(compatible) <= 32
            and all(type(item) is str and re.fullmatch(r"[A-Za-z0-9,._+-]{1,128}", item)
                    for item in compatible) and len(set(compatible)) == len(compatible), "DT 身分清單無效")
    return compatible


def check_config(config):
    life._fields(config, ("schema", "login", "peer_ipv4", "identity_file", "public_key",
                          "rescue_network", "customer_network", "dt_compatible"))
    require(config["schema"] == "bpi-lab-h618-session-v1", "登入設定版本不符")
    peer = ipaddress.IPv4Address(config["peer_ipv4"])
    require(not (peer.is_unspecified or peer.is_multicast or peer.is_loopback), "測試主機位址無效")
    login = config["login"]
    life._fields(login, ("username", "password_file", "new_password_file", "initialize", "skip_user_creation"))
    require(type(login["username"]) is str and re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", login["username"]),
            "登入帳號格式不符")
    require(type(login["initialize"]) is bool and type(login["skip_user_creation"]) is bool,
            "首次登入及略過使用者建立須明示授權")
    for key in ("password_file", "new_password_file"):
        if login[key] is not None:
            life._path(login[key])
    require(not login["initialize"] or login["new_password_file"] is not None, "初始化缺少明示新憑證來源")
    path = str(life._path(config["identity_file"]))
    require(re.fullmatch(r"/[A-Za-z0-9_./:+-]+", path), "SSH 金鑰路徑不得含展開字元")
    public_key(life.checked_bytes(config["public_key"]))
    for field in ("rescue_network", "customer_network"):
        item = config[field]
        require(type(item) is dict and item.get("mode") in ("existing", "wifi"), "網路模式不符")
        life._fields(item, ("mode",) if item["mode"] == "existing" else
                     ("mode", "interface", "ssid", "secret_file"))
        if item["mode"] == "wifi":
            require(type(item["interface"]) is str and
                    re.fullmatch(r"[A-Za-z0-9_.:-]{1,15}", item["interface"]), "Wi-Fi 介面未明示")
            require(type(item["ssid"]) is str and 1 <= len(item["ssid"].encode()) <= 32
                    and all(ord(char) >= 32 for char in item["ssid"]), "SSID 格式不符")
            life._path(item["secret_file"])
    for mode in ("rescue", "customer"):
        _dt_compatible(config, mode)
    return config


def _password(path):
    require(path is not None, "缺少本次登入憑證；不猜測預設帳密")
    value = private_file(path, 1024).removesuffix(b"\n")
    require(1 <= len(value) <= 256 and all(32 <= char < 127 for char in value), "登入憑證格式不符")
    return value


def login(console, config):
    """接續 boot 已消耗的 login 提示；未知流程或逾時均停止，不重送密碼。"""
    console.send(config["username"] + "\n")
    initialized, password_sent, current_sent, repeated, shell_selected, skipped = (False,) * 6
    new_password = None
    prompt_user = config["username"].encode()
    pattern = (rb"(?:Password:|Current password:|\(current\) UNIX password:|"
               rb"Create root password:|New password:|New UNIX password:|"
               rb"Repeat root password:|Retype new password:|Retype new UNIX password:|"
               rb"2\) [^\r\n]+\r?\n|Please provide a username[^\r\n]*: ?|"
               + re.escape(prompt_user) + rb"@[A-Za-z0-9._-]+:[^\r\n]*# ?|Login incorrect|Authentication failure)")
    for _ in range(12):
        prompt = console.expect_regex(pattern, timeout=90)
        token = prompt.matched.strip()
        if token.startswith(prompt_user + b"@"):
            require(not initialized or repeated, "初始化未完成密碼確認")
            require(life._shell(console, "id -u").strip() == b"0", "登入後不是 root 身分")
            return initialized
        if token == b"Password:":
            require(not password_sent and not initialized, "登入重複要求密碼，停止而不猜測")
            console.send(_password(config["password_file"]) + b"\n")
            password_sent = True
        elif token in (b"Current password:", b"(current) UNIX password:"):
            require(config["initialize"] and not current_sent and not initialized, "不允許重複確認舊密碼")
            console.send(_password(config["password_file"]) + b"\n")
            current_sent = True
        elif token.startswith((b"Create root", b"New password", b"New UNIX")):
            require(config["initialize"] and not initialized, "未核定初始化或遠端重複要求新密碼")
            new_password = _password(config["new_password_file"])
            console.send(new_password + b"\n")
            initialized = True
        elif token.startswith((b"Repeat root", b"Retype new")):
            require(initialized and not repeated, "不接受中途殘留的密碼確認")
            console.send(new_password + b"\n")
            repeated = True
        elif token.startswith(b"2)"):
            require(initialized and repeated and not shell_selected and b"1) bash" in prompt.before,
                    "未確認首次 shell 選項")
            console.send("1\n")
            shell_selected = True
        elif token.startswith(b"Please provide"):
            require(initialized and repeated and config["skip_user_creation"] and not skipped,
                    "未核定略過普通使用者建立")
            console.send(b"\x03")
            skipped = True
        else:
            raise ValueError("登入認證失敗；未重試")
    raise ValueError("登入交握超過上限")


# 同一小型唯讀程式分別經 UART 與 SSH 執行；nonce 與 boot_id 一起核對。
IDENTITY = r'''
import json,os,re,subprocess,sys
from pathlib import Path
def read(p): return Path(p).read_text().strip()
def cmd(*args): return subprocess.check_output(args,timeout=5).decode().strip()
mode,nonce=sys.argv[1:]
u=os.uname(); dev=os.stat('/').st_dev
data={'nonce':nonce,'boot_id':read('/proc/sys/kernel/random/boot_id'),
      'kernel':u.release,'architecture':u.machine,'root_dev':str(os.major(dev))+':'+str(os.minor(dev)),
      'dt_compatible':Path('/sys/firmware/devicetree/base/compatible').read_bytes().decode().rstrip('\0').split('\0')}
if mode=='customer':
 base=Path('/sys/dev/block/'+data['root_dev']).resolve(strict=True); parent=base.parent
 assert re.fullmatch(r'mmcblk[0-9]+p1',base.name) and parent.name==base.name[:-2]
 assert read(str(base/'partition'))=='1' and read(str(base/'dev'))==data['root_dev']
 assert not list((parent/'slaves').iterdir()) and not (parent/'partition').exists()
 assert read(str(parent/'device/type'))=='MMC'
 device=str((parent/'device').resolve(strict=True)); controller,sep,_=device.partition('/mmc_host/')
 assert sep
 data.update(cid=read(str(parent/'device/cid')),controller=controller,
             bytes=int(read(str(parent/'size')))*512,uuid=cmd('findmnt','-n','-o','UUID','/'))
else:
 data.update(identity_sha256=__import__('hashlib').sha256(Path('/etc/bpi-rescue.json').read_bytes()).hexdigest(),
             inventory=json.loads(cmd('bpi-rescue','inventory')))
print(json.dumps(data))
'''


def validate_identity(data, mode, components, config, rescue_sha):
    compatible = _dt_compatible(config, mode)
    require(type(data) is dict and re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}",
                                               data.get("boot_id", "")), "Linux boot_id 無效")
    require(data.get("architecture") == "aarch64" and data.get("dt_compatible") == compatible,
            "Linux 架構或 DT 身分不符")
    if mode == "customer":
        expected = {**life.customer.EXPECTED, "uuid": components["root_uuid"],
                    "kernel": components["kernel_release"]}
        require(all(data.get(key) == value for key, value in expected.items()), "Linux 根媒體或原配核心不符")
    else:
        require(data.get("kernel") == life.rescue.KERNEL and data.get("identity_sha256") == rescue_sha,
                "SSH 救援核心或身分檔不符")
        inventory = life.rescue.parse_inventory(json.dumps(data.get("inventory")).encode())
        rows = [line.split(" - ") for line in inventory["mountinfo"].splitlines()]
        require([row[1].split()[0] for row in rows if row[0].split()[4] == "/"] in
                (["rootfs"], ["ramfs"], ["tmpfs"]), "SSH 救援根不在 RAM")
        require(all(row[1].split()[0] in {"rootfs", "ramfs", "tmpfs", "proc", "sysfs", "devtmpfs", "devpts"}
                    and not row[0].split()[2].startswith("179:") for row in rows)
                and len(inventory["swaps"].splitlines()) <= 1, "SSH 救援媒體仍在使用")
        for cid, kind in ((life.rescue.SD_CID, "SD"), (life.customer.EXPECTED["cid"], "MMC")):
            matches = [item for item in inventory["devices"] if item.get("device/cid") == cid]
            require(len(matches) == 1 and matches[0].get("device/type") == kind, "SSH 救援媒體不符")


def _identity_command(mode, nonce):
    return shlex.join(["python3", "-B", "-c", "exec(" + repr(IDENTITY) + ")", mode, nonce])


def ssh_json(ssh, command, deadline):
    """有界 SSH JSON 傳輸；呼叫端須另核對 nonce、工作綁定及板級身分。

    deadline 提供 end、clock()、remaining()；ssh 為本次 write_ssh_config 的結果。
    不輸出原始診斷、不接受額外 SSH 選項，也不放寬主機金鑰驗證。
    """
    for item in (ssh["config"], ssh["known_hosts"]):
        life.checked_bytes(item)
    argv = life.customer.deploy.backup.ssh_command(ssh["config"]["path"], ssh["alias"], {})
    hosts = str(life._path(ssh["known_hosts"]["path"]))
    require(re.fullmatch(r"/[A-Za-z0-9_./:+-]+", hosts), "hostkey 路徑不得含展開字元")
    options = ("UserKnownHostsFile=" + hosts, "GlobalKnownHostsFile=/dev/null", "KnownHostsCommand=none",
               "VerifyHostKeyDNS=no", "ProxyCommand=none", "ProxyJump=none", "IdentityAgent=none",
               "HostKeyAlgorithms=ssh-ed25519")
    argv[-2:-2] = [part for option in options for part in ("-o", option)]
    argv[-1] = command
    captured, code = bytearray(), None
    with closing(life.customer.deploy.backup.ssh_stream(argv, deadline.end, deadline.clock)) as events:
        for kind, value in events:
            deadline.remaining()
            require(code is None, "SSH 退出後仍有事件")
            if kind == "exit":
                require(type(value) is int, "SSH 退出碼無效")
                code = value
            else:
                require(kind in ("stdout", "stderr") and isinstance(value, bytes), "SSH 串流無效")
                require(kind == "stdout" or not value, "嚴格 SSH 含非預期診斷")
                require(len(captured) + len(value) <= 65536, "SSH 身分輸出超界")
                captured.extend(value)
    require(code == 0, "嚴格 SSH 身分核對失敗")
    for item in (ssh["config"], ssh["known_hosts"]):
        life.checked_bytes(item)
    result = safe.parse_manifest(bytes(captured))
    require(type(result) is dict, "SSH 身分回覆必須為 JSON 物件")
    return result


def _ssh_identity(ssh, command, deadline):
    return ssh_json(ssh, command, deadline)


def write_ssh_config(output, *, address, username, identity_file, host_key, alias="lab-session"):
    """建立獨立且排他的新 known_hosts／SSH 設定，不推論 hostkey 的可信來源。

    output 為已存在的私有目錄；host_key 必須由呼叫端本次可信通道取得。
    回傳 config、known_hosts 的固定檔案參照及 alias，不回傳私鑰內容。
    """
    address = str(ipaddress.IPv4Address(address))
    require(not (ipaddress.IPv4Address(address).is_loopback or ipaddress.IPv4Address(address).is_unspecified
                 or ipaddress.IPv4Address(address).is_multicast), "SSH 目的位址不可用")
    require(type(username) is str and re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", username), "SSH 帳號格式不符")
    life._identifier(alias)
    identity_file = str(life._path(identity_file))
    require(re.fullmatch(r"/[A-Za-z0-9_./:+-]+", identity_file), "SSH 金鑰路徑不得含展開字元")
    host = public_key(host_key.encode() if isinstance(host_key, str) else host_key)
    private_file(identity_file)
    output = life._path(output)
    known, ssh_config = output / "known_hosts", output / "ssh-config"
    for path in (known, ssh_config):
        require(re.fullmatch(r"/[A-Za-z0-9_./:+-]+", str(path)), "SSH 證據路徑不得含展開字元")
    values = {"HostName": address, "User": username, "IdentityFile": identity_file, "IdentityAgent": "none",
              "UserKnownHostsFile": str(known), "GlobalKnownHostsFile": "/dev/null",
              "StrictHostKeyChecking": "yes", "HostKeyAlgorithms": "ssh-ed25519", "BatchMode": "yes",
              "IdentitiesOnly": "yes", "PasswordAuthentication": "no", "KbdInteractiveAuthentication": "no",
              "ControlMaster": "no", "ControlPath": "none", "ControlPersist": "no",
              "ProxyCommand": "none", "ProxyJump": "none", "KnownHostsCommand": "none",
              "VerifyHostKeyDNS": "no", "UpdateHostKeys": "no", "ForwardAgent": "no",
              "ClearAllForwardings": "yes", "PermitLocalCommand": "no", "RequestTTY": "no"}
    with safe.open_root(output) as directory:
        info = os.fstat(directory)
        require(info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0, "SSH 證據目錄必須私有")
        for path, blob in ((known, (address + " " + host + "\n").encode()),
                           (ssh_config, ("Host " + alias + "\n" + "".join(
                               "    " + key + " " + value + "\n" for key, value in values.items())).encode())):
            with safe.open_file(directory, path.name, create=True) as stream:
                stream.write(blob)
    return {"config": reference(ssh_config), "alias": alias, "known_hosts": reference(known)}


def connect_wifi(console, item, mode, deadline):
    if item["mode"] == "existing":
        return
    secret = private_file(item["secret_file"], 128).removesuffix(b"\n")
    require(8 <= len(secret) <= 63 and all(32 <= char < 127 for char in secret)
            or len(secret) == 64 and re.fullmatch(rb"[0-9a-fA-F]{64}", secret), "Wi-Fi 憑證格式不符")
    psk = secret.lower() if len(secret) == 64 else hashlib.pbkdf2_hmac(
        "sha1", secret, item["ssid"].encode(), 4096, 32).hex().encode()
    nonce = secrets.token_hex(16)
    path = "/run/bpi-lab-wifi-" + nonce + ".conf"
    # 終端確認 ECHO 關閉後才傳送秘密；不把秘密放入命令或環境。
    script = ("import os,sys,termios\nf=sys.stdin.fileno(); old=termios.tcgetattr(f)\n"
              "new=termios.tcgetattr(f); new[3]&=~(termios.ECHO|termios.ECHONL)\ntry:\n"
              " termios.tcsetattr(f,termios.TCSANOW,new)\n"
              " assert not termios.tcgetattr(f)[3] & (termios.ECHO|termios.ECHONL)\n"
              " print('READY-" + nonce + "',flush=True)\n"
              " s=sys.stdin.buffer.readline(66).strip(); assert len(s)==64; int(s,16)\n"
              " fd=os.open(" + repr(path) + ",os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)\n"
              " with os.fdopen(fd,'wb') as out:\n"
              "  out.write(" + repr(b"ctrl_interface=/run/wpa_supplicant\nnetwork={\n ssid=" +
                          item["ssid"].encode().hex().encode() + b"\n psk=") + "+s+b'\\n}\\n')\n"
              "  out.flush(); os.fsync(out.fileno())\n"
              "finally:\n termios.tcsetattr(f,termios.TCSANOW,old)\n"
              "print('DONE-" + nonce + "',flush=True)")
    console.send("python3 -B -c " + shlex.quote("exec(" + repr(script) + ")") + "\n")
    console.expect_regex(rb"(?:^|\r?\n)READY-" + nonce.encode() + rb"\r?\n", timeout=15)
    console.send(psk + b"\n")
    console.expect_regex(rb"(?:^|\r?\n)DONE-" + nonce.encode() + rb"\r?\n", timeout=15)
    interface = item["interface"]
    if mode == "rescue":
        life._shell(console, shlex.join(["bpi-rescue", "wifi", "--interface", interface, "--config", path]),
                    timeout=min(100, deadline.remaining()))
    else:
        life._shell(console, "systemctl is-active --quiet systemd-networkd")
        content = "[Match]\nName=" + interface + "\n[Network]\nDHCP=ipv4\n[DHCPv4]\nRouteMetric=600\n"
        script = ("import os; p='/run/systemd/network'; os.makedirs(p,exist_ok=True); "
                  "f=os.open(p+'/05-bpi-lab-wifi.network',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600); "
                  "os.write(f," + repr(content.encode()) + "); os.fsync(f); os.close(f)")
        life._shell(console, "python3 -B -c " + shlex.quote(script))
        for argv in (["rfkill", "unblock", "wifi"], ["wpa_supplicant", "-B", "-i", interface, "-c", path],
                     ["networkctl", "reload"], ["networkctl", "reconfigure", interface]):
            life._shell(console, shlex.join(argv))


def establish(console, mode, output, deadline, at_login, *, config, components, request,
              rescue_sha, expected_boot_id=None, initialize_network=True):
    """同一已配對 UART 取得路由、主機公鑰與 boot_id；SSH 再核對同一 Linux。"""
    compatible = _dt_compatible(config, mode)
    output = life._new_directory(Path(output) / "session")
    initialized = login(console, config["login"]) if at_login else False
    require(life._shell(console, "id -u").strip() == b"0", "UART 未登入 root shell")
    nonce = secrets.token_hex(32)
    command = _identity_command(mode, nonce)
    uart_identity = safe.parse_manifest(life._shell(console, command, timeout=30))
    require(uart_identity.get("nonce") == nonce, "UART 身分挑戰不符")
    validate_identity(uart_identity, mode, components, config, rescue_sha)
    if expected_boot_id is not None:
        require(uart_identity["boot_id"] == expected_boot_id, "同次工作發生未記錄的重新啟動")
    if initialize_network:
        connect_wifi(console, config[mode + "_network"], mode, deadline)
    while True:
        addresses = safe.parse_manifest(life._shell(console, "ip -j address"))
        result = console.run_shell("ip -j route get " + config["peer_ipv4"], timeout=10)
        if result.exitcode == 0:
            try:
                interface, address = network.select_local_ipv4(
                    addresses, safe.parse_manifest(result.output), config["peer_ipv4"])
                break
            except ValueError:
                pass
        deadline.pause(2)
    key = public_key(life.checked_bytes(config["public_key"]))
    restricted = 'from="' + config["peer_ipv4"] + '",restrict ' + key + "\n"
    key_directory = "/root/.ssh" if mode == "customer" else "/etc/ssh"
    key_name = "authorized_keys" if mode == "customer" else "rescue_authorized_keys"
    script = ("import os,stat; from pathlib import Path; p=Path(" + repr(key_directory) + "); "
              "assert not p.is_symlink(); p.mkdir(mode=0o700,exist_ok=True); os.chmod(p,0o700); "
              "f=os.open(p/" + repr(key_name) +
              ",os.O_RDWR|os.O_APPEND|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK,0o600); "
              "s=os.fstat(f); assert stat.S_ISREG(s.st_mode) and s.st_nlink==1 and s.st_size<=65536; "
              "b=" + repr(restricted.encode()) + "; old=os.read(f,65536); "
              "assert not old or old.endswith(b'\\n'); "
              "os.write(f,b) if b not in old.splitlines(keepends=True) else None; "
              "os.fchmod(f,0o600); os.fsync(f); os.close(f)")
    life._shell(console, "python3 -B -c " + shlex.quote(script))
    host_path = "/etc/ssh/ssh_host_ed25519_key.pub" if mode == "customer" else "/run/ssh/ssh_host_ed25519_key.pub"
    while mode == "customer":
        ready = console.run_shell(
            'case "$(systemctl show armbian-firstrun -p SubState --value)" in exited|dead) '
            'test -s /etc/ssh/ssh_host_ed25519_key.pub && systemctl is-active --quiet ssh;; *) false;; esac',
            timeout=10)
        if ready.exitcode == 0:
            break
        deadline.pause(2)
    host = public_key(life._shell(console, "cat " + host_path))
    ssh = write_ssh_config(output, address=address, username=config["login"]["username"],
                           identity_file=config["identity_file"], host_key=host)
    observed = _ssh_identity(ssh, command, deadline)
    require(observed == uart_identity, "SSH 與同次 UART 的 Linux 身分不一致")
    if mode == "customer":
        collected = linux.collect(ssh_config=ssh["config"]["path"], alias=ssh["alias"],
                                  known_hosts=ssh["known_hosts"]["path"],
                                  timeout=min(60, deadline.remaining()))
        expected = {"schema": linux.EXPECTED_SCHEMA, "architecture": "arm64",
                    "kernel_release": components["kernel_release"], "dt_compatible": compatible,
                    "root": {**life.customer.EXPECTED, "uuid": components["root_uuid"], "media_type": "MMC"}}
        checked = linux.validate(collected, expected)
        life._save(output, "linux-collection.json", collected)
        life._save(output, "linux-validation.json", checked)
        # 失敗服務留給 smoke 判定；不能藉此跳過任何身分欄位。
        require(all(item["status"] == "passed" for item in checked["checks"]
                    if item["check"] != "failed_services"), "Linux 完整身分核對未通過")
    require(_ssh_identity(ssh, command, deadline) == observed, "SSH 核對期間 Linux 已改變")
    for item in (ssh["config"], ssh["known_hosts"]):
        life.checked_bytes(item)
    result = {"schema": "bpi-lab-h618-session-result-v1", "binding": binding(request), "mode": mode,
              "boot_id": observed["boot_id"], "nonce": nonce, "ssh": ssh,
              "interface": interface, "address": address, "strict_ssh_verified": True,
              "network_verified": True, "identity": observed,
              "first_login_initialized": initialized, "hardware_validated": False}
    life._save(output, "session.json", result)
    deadline.remaining()
    return result


def recheck(record, request, deadline):
    """資料階段之後確認未換 boot；只使用本次私有 SSH 檔案與新挑戰。"""
    require(record.get("binding") == binding(request), "SSH 重驗不可跨嘗試")
    for item in (record["ssh"]["config"], record["ssh"]["known_hosts"]):
        life.checked_bytes(item)
    nonce = secrets.token_hex(32)
    observed = _ssh_identity(record["ssh"], _identity_command(record["mode"], nonce), deadline)
    require(observed == {**record["identity"], "nonce": nonce}, "階段執行期間 Linux 身分改變")


def reference(path):
    path = Path(path)
    with safe.open_root(path.parent) as directory:
        digest, _ = safe.fingerprint(directory, path.name, limit=1024**2)
    return {"path": str(path), "sha256": digest["sha256"]}
