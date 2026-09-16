"""站點設定與有界適配器契約；不直接開啟 UART、電源或媒體設備。

validate_report 只核對適配器的宣告、工作綁定及必要欄位，不自主重驗
原始硬體證據。模擬結果不可計入硬體通過數。外部程式由操作員提供，
程序群組及輸出限制不是沙箱，也不能證明該程式或其相依項的硬體安全。

external-v1 使用 argv 與 sha256；僅入口檔案受摘要及不可變快照保護，
直譯器、函式庫與 argv 引用的檔案仍屬操作員核定範圍。外部執行需要
Linux 的 O_PATH、memfd 與 /proc。資格證據和入口均拒絕路徑中的符號連結。
hardware 資源使用具名穩定身分或 by-id 路徑，不解析或開啟設備。
停用硬體範本可省略硬體身分、引導摘要、資源、適配器、資格及授權；
已填欄位仍需合法。每次執行需使用未有證據檔的獨立 evidence_dir。

資格文件上限 1 MiB，schema 為 bpi-lab-qualification-v1，必須綁定
station_id、hardware_id、resources、media（等於 resources.media）、
boot_config_sha256、adapter_sha256、test_version；hardware_validated、
rescue_verified、single_image_cycle_verified 均須為 true。
source_evidence 至少含一筆 {path, sha256}，path 為絕對路徑。
本模組不開啟這些原始證據；資格契約通過不代表原始證據已被自主重驗。
"""

import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import time


STAGES = ("preflight", "deploy", "boot", "smoke", "recovery")
STATUSES = ("passed", "failed", "blocked")
MAX_OUTPUT_BYTES = 1024 * 1024
MAX_FILE_BYTES = 64 * 1024 * 1024
BINDINGS = (
    "work_key", "attempt_id", "stage", "station_id", "hardware_id",
    "image_sha256", "boot_config_sha256", "test_version", "mode",
)
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_BOARD = re.compile(r"bpi-[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_ATTESTATIONS = (
    "media_identity_verified", "backup_verified", "full_readback_verified",
    "compressed_sha256_verified", "customer_kernel_verified", "rescue_verified",
    "resume_state_verified",
)


class StationError(ValueError):
    """設定、證據或適配器契約未通過；呼叫端不可當成階段成功。"""


def _mapping(value, field):
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise StationError(f"{field} 必須是字串鍵值的物件")
    return value


def _text(value, field):
    if (type(value) is not str or not value.strip() or value != value.strip()
            or len(value) > 4096 or any(ord(char) < 32 for char in value)):
        raise StationError(f"{field} 必須是非空且無控制字元的字串")
    return value


def _sha(value, field):
    if type(value) is not str or not _HASH.fullmatch(value):
        raise StationError(f"{field} 必須是小寫 SHA-256")


def _number(value, field, minimum, maximum):
    if (type(value) not in (int, float) or not minimum <= value <= maximum
            or not math.isfinite(value)):
        raise StationError(f"{field} 不在允許的有限數值範圍")


def _fields(data, required, allowed, field):
    _mapping(data, field)
    missing, unknown = set(required) - data.keys(), data.keys() - set(allowed)
    if missing or unknown:
        raise StationError(f"{field} 缺少欄位 {sorted(missing)}；未知欄位 {sorted(unknown)}")


def _json_bytes(data):
    try:
        encoded = json.dumps(data, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError, UnicodeError) as exc:
        raise StationError("資料必須能以嚴格 JSON 編碼") from exc
    if len(encoded) > MAX_OUTPUT_BYTES:
        raise StationError("JSON 資料超過 1 MiB")
    return encoded


def _json_loads(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise StationError(f"JSON 重複鍵值：{key}")
            result[key] = value
        return result

    def constant(value):
        raise StationError(f"JSON 不允許非有限數值：{value}")

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise StationError("適配器輸出不是唯一且有效的 JSON") from exc


def _absolute_path(value, field):
    _text(value, field)
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise StationError(f"{field} 必須是無上層跳轉的絕對路徑")
    return path


def _resource(value, kind, mode):
    _text(value, f"resources.{kind}")
    if mode == "simulation":
        if not re.fullmatch(r"sim:[A-Za-z0-9][A-Za-z0-9._:/@+-]*", value):
            raise StationError("模擬資源必須使用 sim: 穩定身分")
        return
    prefixes = {"uart": ("uart:", "serial:"),
                "power": ("power:", "pdu:", "relay:"),
                "media": ("media:", "cid:", "wwn:")}[kind]
    by_id = {"uart": "/dev/serial/by-id/", "media": "/dev/disk/by-id/"}.get(kind)
    if by_id and value.startswith(by_id):
        identity = value[len(by_id):]
        valid = bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:+-]*", identity))
    else:
        identity = value.partition(":")[2]
        valid = value.startswith(prefixes) and bool(
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@+=,-]*", identity))
    if not valid or identity.lower() in {"unknown", "unset", "todo", "template", "none"}:
        raise StationError(f"resources.{kind} 不是已明示的穩定唯一身分")


def validate_station(data: dict) -> dict:
    """驗證純資料，不讀檔；成功原樣回傳同一個 dict，不填預設值。"""
    base = {"schema", "station_id", "mode", "enabled", "board",
            "compatible_boards", "test_version", "timeout_seconds"}
    safety = {"hardware_id", "boot_config_sha256", "resources", "adapter",
              "qualification", "authorization"}
    _fields(data, base, base | safety, "station")
    if data["schema"] != "bpi-lab-station-v1":
        raise StationError("站點 schema 不符")
    if data["mode"] not in ("simulation", "hardware") or type(data["enabled"]) is not bool:
        raise StationError("站點 mode 或 enabled 無效")
    for field in ("station_id", "board", "test_version"):
        _text(data[field], field)
    boards = data["compatible_boards"]
    if (type(boards) is not list or not boards
            or any(type(board) is not str or not _BOARD.fullmatch(board) for board in boards)
            or len(set(boards)) != len(boards)):
        raise StationError("compatible_boards 必須是不重複的 bpi-* 來源目錄清單")
    _number(data["timeout_seconds"], "timeout_seconds", 0.001, 86400)
    incomplete = data["mode"] == "hardware" and not data["enabled"]
    required = set() if incomplete else safety - {"qualification", "authorization"}
    if data["mode"] == "hardware" and data["enabled"]:
        required |= {"qualification", "authorization"}
    if required - data.keys():
        raise StationError(f"站點缺少必要安全欄位：{sorted(required - data.keys())}")
    if "hardware_id" in data:
        _text(data["hardware_id"], "hardware_id")
        if data["mode"] == "hardware" and data["hardware_id"].startswith("sim:"):
            raise StationError("硬體身分不得使用 sim:")
        if not incomplete and data["mode"] == "hardware" and data["hardware_id"].startswith("pending-"):
            raise StationError("硬體身分尚未配對")
    if "boot_config_sha256" in data:
        _sha(data["boot_config_sha256"], "boot_config_sha256")
    if "resources" in data:
        resources = data["resources"]
        _fields(resources, () if incomplete else ("uart", "power", "media"),
                ("uart", "power", "media"), "resources")
        for kind, value in resources.items():
            if not (incomplete and type(value) is str
                    and re.fullmatch(r"pending:[A-Za-z0-9._-]+:" + kind, value)):
                _resource(value, kind, data["mode"])
        if len(set(resources.values())) != len(resources):
            raise StationError("站點的 UART、電源及媒體身分不可重複")
    if "adapter" in data:
        adapter = _mapping(data["adapter"], "adapter")
        kind = adapter.get("kind")
        if kind == "simulator-v1":
            _fields(adapter, ("kind",), ("kind", "outcomes", "delay_seconds"), "adapter")
            if data["mode"] != "simulation":
                raise StationError("硬體站點不得使用 simulator-v1")
            outcomes = _mapping(adapter.get("outcomes", {}), "adapter.outcomes")
            if any(stage not in STAGES or status not in STATUSES for stage, status in outcomes.items()):
                raise StationError("模擬階段或結果無效")
            _number(adapter.get("delay_seconds", 0), "adapter.delay_seconds", 0, 86400)
        elif kind == "external-v1":
            _fields(adapter, ("kind",) if incomplete else ("kind", "argv", "sha256"),
                    ("kind", "argv", "sha256"), "adapter")
            if "argv" in adapter:
                argv = adapter["argv"]
                if type(argv) is not list or (not argv and not incomplete) or len(argv) > 256:
                    raise StationError("adapter.argv 必須是非空且有界的參數清單")
                for arg in argv:
                    _text(arg, "adapter.argv")
                if argv:
                    _absolute_path(argv[0], "adapter.argv[0]")
            if "sha256" in adapter:
                _sha(adapter["sha256"], "adapter.sha256")
        else:
            raise StationError("缺少或未知的適配器種類")
    if "qualification" in data:
        qualification = data["qualification"]
        fields = ("status", "evidence_sha256", "evidence_path")
        _fields(qualification, () if incomplete else fields, fields, "qualification")
        allowed_statuses = ("qualified", "awaiting_hardware") if incomplete else ("qualified",)
        if "status" in qualification and qualification["status"] not in allowed_statuses:
            raise StationError("資格尚未核定")
        if "evidence_sha256" in qualification:
            _sha(qualification["evidence_sha256"], "qualification.evidence_sha256")
        if "evidence_path" in qualification:
            _absolute_path(qualification["evidence_path"], "qualification.evidence_path")
    if "authorization" in data:
        authorization = data["authorization"]
        fields = ("userarea_write", "hardware_id", "media_identity", "backup_sha256", "record")
        _fields(authorization, () if incomplete else fields, fields, "authorization")
        if "userarea_write" in authorization:
            allowed = authorization["userarea_write"]
            if type(allowed) is not bool or (not incomplete and not allowed):
                raise StationError("未明確授權 userarea 寫入")
        for field in ("hardware_id", "media_identity", "record"):
            if field in authorization:
                _text(authorization[field], f"authorization.{field}")
        if "backup_sha256" in authorization:
            _sha(authorization["backup_sha256"], "authorization.backup_sha256")
        if ("hardware_id" in authorization and "hardware_id" in data
                and authorization["hardware_id"] != data["hardware_id"]):
            raise StationError("授權的 hardware_id 與站點不符")
        media = data.get("resources", {}).get("media")
        if media is not None and "media_identity" in authorization and authorization["media_identity"] != media:
            raise StationError("授權的 media_identity 與站點不符")
    if data["mode"] == "hardware" and not incomplete:
        digests = (data["boot_config_sha256"], data["adapter"].get("sha256"),
                   data["qualification"]["evidence_sha256"], data["authorization"]["backup_sha256"])
        if "0" * 64 in digests:
            raise StationError("啟用硬體不得使用未核定的全零摘要")
    _json_bytes(data)
    return data


def station_digest(data: dict) -> str:
    """完整設定以排序鍵值的緊密 UTF-8 JSON 計算 SHA-256，不依賴檔案狀態。"""
    return hashlib.sha256(_json_bytes(validate_station(data))).hexdigest()


def resource_keys(data: dict) -> list[str]:
    """回傳排序後的站點、板號、共用資源鎖鍵；相同身分不因站點不同而分離。"""
    validate_station(data)
    keys = {f"station:{data['station_id']}"}
    if "hardware_id" in data:
        keys.add(f"hardware:{data['hardware_id']}")
    keys.update(f"resource:{value}" for value in data.get("resources", {}).values())
    return sorted(keys)


def _validate_request(request):
    required = {"schema", "image", "image_root", *BINDINGS}
    _fields(request, required, required | {"resume"}, "request")
    if request["schema"] != "bpi-lab-request-v1" or request["stage"] not in STAGES:
        raise StationError("請求 schema 或 stage 不符")
    if request["mode"] not in ("simulation", "hardware"):
        raise StationError("請求 mode 無效")
    for field in BINDINGS + ("image_root",):
        _text(request[field], f"request.{field}")
    for field in ("image_sha256", "boot_config_sha256"):
        _sha(request[field], f"request.{field}")
    image = _mapping(request["image"], "request.image")
    board = image.get("board")
    if type(board) is not str or not _BOARD.fullmatch(board):
        raise StationError("映像缺少合法的來源 board")
    for field in ("sha256", "image_sha256", "expected_sha256"):
        if field in image and image[field] != request["image_sha256"]:
            raise StationError("映像宣告摘要與請求不符")
    if "resume" in request:
        resume = request["resume"]
        _fields(resume, ("next_stage", "previous_reports"),
                ("next_stage", "previous_reports"), "request.resume")
        if request["stage"] != "preflight" or resume["next_stage"] not in STAGES:
            raise StationError("resume 僅能以 preflight 核對後續階段")
        previous = resume["previous_reports"]
        if type(previous) is not list:
            raise StationError("resume.previous_reports 必須是清單")
        seen = set()
        for item in previous:
            _fields(item, ("stage", "sha256", "path"), ("stage", "sha256", "path"), "previous_reports")
            if item["stage"] not in STAGES:
                raise StationError("歷史報告階段無效")
            _sha(item["sha256"], "previous_reports.sha256")
            _text(item["path"], "previous_reports.path")
            if item["path"] in seen:
                raise StationError("歷史報告路徑重複")
            seen.add(item["path"])
    _json_bytes(request)
    return request


def validate_report(report: dict, request: dict) -> dict:
    """回傳通過契約的原 report；真實硬體證據仍由已核定適配器負責核對。"""
    _validate_request(request)
    _mapping(report, "report")
    if report.get("schema") != "bpi-lab-stage-v1":
        raise StationError("報告 schema 不符")
    for field in BINDINGS:
        if type(report.get(field)) is not str or report[field] != request[field]:
            raise StationError(f"報告的 {field} 與本次請求不符")
    if report.get("status") not in STATUSES or type(report.get("hardware_validated")) is not bool:
        raise StationError("報告 status 或 hardware_validated 無效")
    if "synthetic" in report and type(report["synthetic"]) is not bool:
        raise StationError("synthetic 必須是布林值")
    hardware = request["mode"] == "hardware"
    if not hardware and report["hardware_validated"]:
        raise StationError("模擬報告不得宣稱硬體驗證")
    if hardware and report.get("synthetic", False):
        raise StationError("合成結果不得冒充硬體報告")
    for field in _ATTESTATIONS:
        if field in report and type(report[field]) is not bool:
            raise StationError(f"{field} 必須是布林值")
    if report["status"] == "passed":
        if "resume" in request:
            if (report.get("resume_state_verified") is not True
                    or report.get("resume_next_stage") != request["resume"]["next_stage"]):
                raise StationError("續作前置核對未驗證目前狀態或後續階段")
        if hardware:
            if not report["hardware_validated"]:
                raise StationError("硬體通過報告缺少 hardware_validated")
            flags = {
                "preflight": ("media_identity_verified", "backup_verified"),
                "deploy": ("full_readback_verified", "compressed_sha256_verified"),
                "boot": ("customer_kernel_verified",),
                "smoke": (), "recovery": ("rescue_verified",),
            }[request["stage"]]
            if any(report.get(flag) is not True for flag in flags):
                raise StationError("硬體通過報告缺少必要階段核對")
            if request["stage"] == "smoke":
                checks = _mapping(report.get("checks"), "report.checks")
                if not checks or any(not key.strip() or value is not True for key, value in checks.items()):
                    raise StationError("smoke 檢查必須非空且全部明確通過")
    _json_bytes(report)
    return report


def _directory(path, create=False):
    """以目錄描述符逐段開啟，不追隨任一層的符號連結。"""
    path = Path(os.path.abspath(path))
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                    os.fsync(fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _open_regular(path, executable=False):
    parent = _directory(path.parent)
    pinned = None
    try:
        info = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode):
            raise StationError("證據與適配器入口只能是一般檔案，不得是連結或設備")
        if not hasattr(os, "O_PATH"):
            raise StationError("平台不支援安全的一般檔案描述符")
        pinned = os.open(path.name, os.O_PATH | os.O_NOFOLLOW, dir_fd=parent)
        pinned_info = os.fstat(pinned)
        if (not stat.S_ISREG(pinned_info.st_mode)
                or (info.st_dev, info.st_ino) != (pinned_info.st_dev, pinned_info.st_ino)):
            raise StationError("開啟前檔案身分已變動")
        if pinned_info.st_size > MAX_FILE_BYTES:
            raise StationError("資格證據或適配器入口超過 64 MiB")
        if executable and (not os.access(f"/proc/self/fd/{pinned}", os.X_OK, effective_ids=True)
                           or pinned_info.st_mode & 0o6022
                           or pinned_info.st_uid not in (0, os.geteuid())):
            raise StationError("適配器入口必須可執行、擁有者可信且無群組或其他人寫入權")
        return os.fdopen(os.open(f"/proc/self/fd/{pinned}", os.O_RDONLY), "rb")
    finally:
        if pinned is not None:
            os.close(pinned)
        os.close(parent)


def _digest_file(source, destination=None):
    digest, total = hashlib.sha256(), 0
    while True:
        chunk = source.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_FILE_BYTES:
            raise StationError("讀取檔案超過 64 MiB")
        digest.update(chunk)
        if destination is not None:
            destination.write(chunk)
    return digest.hexdigest()


def _verify_qualification(station):
    qualification = station["qualification"]
    with _open_regular(Path(qualification["evidence_path"])) as source:
        raw = source.read(MAX_OUTPUT_BYTES + 1)
    if len(raw) > MAX_OUTPUT_BYTES:
        raise StationError("資格文件超過 1 MiB")
    if hashlib.sha256(raw).hexdigest() != qualification["evidence_sha256"]:
        raise StationError("資格證據 SHA-256 不符")
    evidence = _json_loads(raw)
    bindings = ("station_id", "hardware_id", "boot_config_sha256", "test_version")
    flags = ("hardware_validated", "rescue_verified", "single_image_cycle_verified")
    fields = {"schema", "resources", "media", "adapter_sha256", "source_evidence", *bindings, *flags}
    _fields(evidence, fields, fields, "qualification.evidence")
    if evidence["schema"] != "bpi-lab-qualification-v1":
        raise StationError("資格文件 schema 不符")
    for field in bindings:
        if type(evidence[field]) is not str or evidence[field] != station[field]:
            raise StationError(f"資格文件的 {field} 與站點不符")
    _mapping(evidence["resources"], "qualification.evidence.resources")
    if evidence["resources"] != station["resources"] or evidence["media"] != station["resources"]["media"]:
        raise StationError("資格文件的資源或媒體與站點不符")
    if evidence["adapter_sha256"] != station["adapter"]["sha256"]:
        raise StationError("資格文件的適配器摘要與站點不符")
    if any(evidence[flag] is not True for flag in flags):
        raise StationError("資格文件未明確完成實板、救援及單映像循環驗證")
    references = evidence["source_evidence"]
    if type(references) is not list or not references:
        raise StationError("資格文件必須至少參照一筆原始證據")
    seen = set()
    for reference in references:
        _fields(reference, ("path", "sha256"), ("path", "sha256"), "source_evidence")
        path = str(_absolute_path(reference["path"], "source_evidence.path"))
        _sha(reference["sha256"], "source_evidence.sha256")
        if reference["sha256"] == "0" * 64 or path in seen:
            raise StationError("原始證據參照使用未核定摘要或重複路徑")
        seen.add(path)
    _json_bytes(evidence)


def _snapshot_executable(adapter):
    if not hasattr(os, "memfd_create"):
        raise StationError("平台不支援不可變的適配器執行快照")
    with _open_regular(Path(adapter["argv"][0]), executable=True) as source:
        fd = os.memfd_create("bpi-lab-adapter", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        try:
            with os.fdopen(os.dup(fd), "wb") as destination:
                actual = _digest_file(source, destination)
            if actual != adapter["sha256"]:
                raise StationError("適配器入口 SHA-256 不符")
            os.fchmod(fd, 0o500)
            fcntl.fcntl(fd, fcntl.F_ADD_SEALS, fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW
                        | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
            return fd
        except BaseException:
            os.close(fd)
            raise


def _external(adapter, request_bytes, timeout, capture=None):
    fd = _snapshot_executable(adapter)
    proc = None
    selector = selectors.DefaultSelector()
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    problem = None
    try:
        deadline = time.monotonic() + timeout
        proc = subprocess.Popen(adapter["argv"], executable=f"/proc/self/fd/{fd}",
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, start_new_session=True,
                                close_fds=True, pass_fds=(fd,), shell=False)
        for stream, name, event in ((proc.stdin, "stdin", selectors.EVENT_WRITE),
                                    (proc.stdout, "stdout", selectors.EVENT_READ),
                                    (proc.stderr, "stderr", selectors.EVENT_READ)):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, event, name)
        pending = memoryview(request_bytes + b"\n")
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                problem = "適配器執行逾時"
                break
            for key, _ in selector.select(remaining):
                stream, name = key.fileobj, key.data
                if name == "stdin":
                    try:
                        pending = pending[os.write(stream.fileno(), pending[:65536]):]
                    except BrokenPipeError:
                        pending = pending[:0]
                    except BlockingIOError:
                        continue
                    if not pending:
                        selector.unregister(stream)
                        stream.close()
                else:
                    try:
                        chunk = os.read(stream.fileno(), 65536)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(stream)
                        stream.close()
                        continue
                    room = MAX_OUTPUT_BYTES - len(captured[name])
                    captured[name].extend(chunk[:room])
                    if capture is not None and room:
                        capture(name, chunk[:room])
                    if len(chunk) > room:
                        problem = f"適配器 {name} 超過 1 MiB"
                        break
            if problem:
                break
        if problem is None:
            try:
                code = proc.wait(timeout=max(0, deadline - time.monotonic()))
                if code != 0:
                    problem = f"適配器退出碼非零：{code}"
            except subprocess.TimeoutExpired:
                problem = "適配器執行逾時"
    finally:
        selector.close()
        if proc is not None:
            # 即使入口先退出，也終止繼承群組的子程序，不讓其繼續操作。
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                stream.close()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                problem = "適配器程序群組終止後仍未退出"
        os.close(fd)
    return bytes(captured["stdout"]), bytes(captured["stderr"]), problem


def _reserve_evidence(evidence_dir):
    directory = _directory(evidence_dir, create=True)
    files = {}
    names = ("request.json", "stdout.log", "stderr.log", "response.json")
    try:
        for name in names:
            try:
                os.stat(name, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                continue
            raise StationError(f"證據已存在，不得覆寫：{name}")
        for name in names:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory)
            files[name] = os.fdopen(fd, "wb")
        os.fsync(directory)
        return directory, files
    except BaseException:
        for output in files.values():
            output.close()
        os.close(directory)
        raise


def run_stage(station: dict, request: dict, evidence_dir) -> dict:
    """執行一階段並先持久保存四份新證據；任何不可信結果均丟 StationError。

    合法的 failed/blocked 報告直接回傳；逾時、退出碼、摘要或報告契約
    錯誤會丟例外，response.json 保存可取得的回應，否則保存錯誤診斷。
    request.image 只作資料傳遞；本模組不讀映像、不執行其文字。
    """
    validate_station(station)
    _validate_request(request)
    station = _json_loads(_json_bytes(station))
    request = _json_loads(_json_bytes(request))
    if not station["enabled"]:
        raise StationError("站點已停用，禁止啟動")
    for field in ("station_id", "hardware_id", "boot_config_sha256", "test_version", "mode"):
        if station[field] != request[field]:
            raise StationError(f"請求 {field} 與站點設定不符")
    if request["image"]["board"] not in station["compatible_boards"]:
        raise StationError("映像來源 board 不在站點允許清單")
    try:
        directory, files = _reserve_evidence(evidence_dir)
    except OSError as exc:
        raise StationError("無法排他建立證據檔案") from exc
    stdout, stderr = b"", b""
    response = {"schema": "bpi-lab-adapter-error-v1", "error": "執行未完成"}

    def capture(name, chunk):
        output = files[name + ".log"]
        output.write(chunk)
        output.flush()

    try:
        files["request.json"].write(_json_bytes(request) + b"\n")
        files["request.json"].flush()
        os.fsync(files["request.json"].fileno())
        if station["mode"] == "hardware":
            _verify_qualification(station)
        adapter = station["adapter"]
        if adapter["kind"] == "simulator-v1":
            delay, timeout = adapter.get("delay_seconds", 0), station["timeout_seconds"]
            time.sleep(min(delay, timeout))
            if delay >= timeout:
                raise StationError("模擬適配器執行逾時")
            response = {"schema": "bpi-lab-stage-v1", **{field: request[field] for field in BINDINGS},
                        "status": adapter.get("outcomes", {}).get(request["stage"], "passed"),
                        "hardware_validated": False, "synthetic": True}
            if "resume" in request and response["status"] == "passed":
                response.update(resume_state_verified=True,
                                resume_next_stage=request["resume"]["next_stage"])
            stdout = _json_bytes(response) + b"\n"
        else:
            stdout, stderr, problem = _external(adapter, _json_bytes(request),
                                                station["timeout_seconds"], capture=capture)
            if problem:
                raise StationError(problem)
            response = _json_loads(stdout.decode("utf-8"))
        return validate_report(response, request)
    except (OSError, ValueError, RecursionError) as exc:
        if type(response) is dict and response.get("schema") == "bpi-lab-adapter-error-v1":
            response["error"] = str(exc)
        if isinstance(exc, StationError):
            raise
        raise StationError(f"階段執行或證據處理失敗：{exc}") from exc
    finally:
        try:
            if files["stdout.log"].tell() == 0:
                files["stdout.log"].write(stdout)
            if files["stderr.log"].tell() == 0:
                files["stderr.log"].write(stderr)
            try:
                response_bytes = _json_bytes(response)
            except StationError:
                response_bytes = _json_bytes({"schema": "bpi-lab-adapter-error-v1",
                                              "error": "回應無法正規化為 UTF-8 JSON，原始輸出另存"})
            files["response.json"].write(response_bytes + b"\n")
            for output in files.values():
                output.flush()
                os.fsync(output.fileno())
            os.fsync(directory)
        except OSError as exc:
            raise StationError("證據未能完整持久保存") from exc
        finally:
            for output in files.values():
                output.close()
            os.close(directory)
