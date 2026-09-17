"""跨板工作佇列；持久紀錄、資源排他與有界階段執行，不內建燒錄命令。"""

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
import uuid

import bpi_lab_station as station_api


STAGES = ("preflight", "deploy", "boot", "smoke", "recovery")
LOCK_ROOT = Path("/var/tmp/bpi-lab-locks")
MAX_JSON = 32 * 1024**2
BINDING_FIELDS = ("station_sha256", "hardware_id", "image_sha256", "source_metadata_sha256",
                  "image_root", "boot_config_sha256", "test_version", "mode")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def encode(data):
    return (json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode()


def digest(data):
    return hashlib.sha256(encode(data)).hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "JSON 欄位重複")
        result[key] = value
    return result


def decode(blob):
    require(len(blob) <= MAX_JSON, "JSON 超過大小限制")
    return json.loads(blob, object_pairs_hook=unique_object,
                      parse_constant=lambda _: require(False, "JSON 數值無效"))


def safe_directory(path, create=False):
    path = Path(path).absolute()
    require(".." not in path.parts, "目錄不可含有 ..")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if create:
            try:
                current.mkdir(mode=0o700)
            except FileExistsError:
                pass
        require(stat.S_ISDIR(current.lstat().st_mode), "目錄不可經過符號連結或裝置")
    return path


def read_blob(path):
    path = Path(path).absolute()
    safe_directory(path.parent)
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_size <= MAX_JSON, "只接受有界的一般 JSON 檔案")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "JSON 檔案型別改變")
        blob = stream.read(MAX_JSON + 1)
        require(len(blob) <= MAX_JSON, "JSON 超過大小限制")
        return blob


def read_json(path):
    return decode(read_blob(path))


def save_json(path, data):
    path = Path(path).absolute()
    safe_directory(path.parent, create=True)
    blob = encode(data)
    require(len(blob) <= MAX_JSON, "JSON 超過大小限制")
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                           0o600), "wb") as stream:
        stream.write(blob)
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return {"path": str(path), "sha256": hashlib.sha256(blob).hexdigest()}


def connect(path):
    path = Path(path).absolute()
    safe_directory(path.parent, create=True)
    if path.exists() or path.is_symlink():
        require(stat.S_ISREG(path.lstat().st_mode), "資料庫必須為一般檔案")
    db = sqlite3.connect(path, timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    try:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        require(version in (0, 1), "佇列資料庫版本不支援")
        if version == 0:
            require(not db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(),
                    "不修改未識別的既有資料庫")
    except BaseException:
        db.close()
        raise
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=FULL")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS images (
            image_id TEXT PRIMARY KEY, root TEXT NOT NULL, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS history_refs (
            image_sha256 TEXT NOT NULL, source_sha256 TEXT NOT NULL,
            source_path TEXT NOT NULL, body TEXT NOT NULL,
            PRIMARY KEY(image_sha256,source_sha256));
        CREATE TABLE IF NOT EXISTS stations (
            station_id TEXT PRIMARY KEY, config TEXT NOT NULL, digest TEXT NOT NULL,
            health TEXT NOT NULL DEFAULT 'ready');
        CREATE TABLE IF NOT EXISTS jobs (
            work_key TEXT PRIMARY KEY, station_id TEXT NOT NULL REFERENCES stations,
            mode TEXT NOT NULL, state TEXT NOT NULL, body TEXT NOT NULL,
            priority TEXT NOT NULL, attempt_id TEXT, cursor INTEGER NOT NULL DEFAULT 0,
            inflight TEXT, created REAL NOT NULL, updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS attempts (
            attempt_id TEXT PRIMARY KEY, work_key TEXT NOT NULL REFERENCES jobs,
            started REAL NOT NULL, finished REAL, result TEXT);
        CREATE TABLE IF NOT EXISTS reports (
            report_id INTEGER PRIMARY KEY, work_key TEXT NOT NULL REFERENCES jobs,
            attempt_id TEXT NOT NULL REFERENCES attempts, stage TEXT NOT NULL,
            status TEXT NOT NULL, path TEXT NOT NULL UNIQUE, sha256 TEXT NOT NULL,
            is_resume_guard INTEGER NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS events (
            event_id INTEGER PRIMARY KEY, work_key TEXT, kind TEXT NOT NULL,
            body TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS failure_branches (
            work_key TEXT PRIMARY KEY REFERENCES jobs, stage TEXT NOT NULL, state TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(station_id, state, priority);
        PRAGMA user_version=1;
    """)
    return db


@contextmanager
def transaction(db):
    db.execute("BEGIN IMMEDIATE")
    try:
        yield
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise


def event(db, key, kind, data):
    db.execute("INSERT INTO events(work_key,kind,body,created) VALUES (?,?,?,?)",
               (key, kind, encode(data).decode(), time.time()))


def import_catalog(db, catalog):
    require(catalog.get("schema") == "bpi-lab-catalog-v1" and
            catalog.get("hardware_validated") is False, "必須匯入離線清單")
    require(isinstance(catalog.get("entries"), list), "映像清單無效")
    root = str(safe_directory(catalog["root"]))
    seen = {}
    with transaction(db):
        for item in catalog["entries"]:
            key = item["image_id"]
            require(isinstance(key, str) and key not in seen, "映像識別重複")
            seen[key] = digest(item)
            path = Path(item["relative_path"])
            require(not path.is_absolute() and ".." not in path.parts, "映像路徑越界")
            db.execute("INSERT INTO images VALUES (?,?,?) ON CONFLICT(image_id) "
                       "DO UPDATE SET root=excluded.root,body=excluded.body",
                       (key, root, encode(item).decode()))
        # 同一根目錄採完整快照；保留歷史工作，但不讓已移除或改版的來源再次排隊。
        for row in db.execute("SELECT image_id FROM images WHERE root=?", (root,)).fetchall():
            if row[0] not in seen:
                db.execute("DELETE FROM images WHERE image_id=?", (row[0],))
        for row in db.execute("SELECT work_key,body FROM jobs WHERE state IN ('queued','review_required','metadata_blocked')").fetchall():
            body = decode(row["body"])
            if body["image_root"] == root and seen.get(body["image"]["image_id"]) != body["source_metadata_sha256"]:
                db.execute("UPDATE jobs SET state='superseded',updated=? WHERE work_key=?", (time.time(), row["work_key"]))
                event(db, row["work_key"], "source_superseded", {"catalog_sha256": digest(catalog)})
        event(db, None, "catalog_imported", {"entries": len(seen), "sha256": digest(catalog),
                                             "hardware_validated": False})
    return len(seen)


def register_station(db, data):
    station_api.validate_station(data)
    key = data["station_id"]
    signature = station_api.station_digest(data)
    with transaction(db):
        old = db.execute("SELECT * FROM stations WHERE station_id=?", (key,)).fetchone()
        busy = db.execute("SELECT 1 FROM jobs WHERE station_id=? AND state IN "
                          "('running','interrupted')", (key,)).fetchone()
        require(not busy or old["digest"] == signature, "站點有未結束工作，不能更換設定")
        if old and old["digest"] != signature:
            for row in db.execute("SELECT work_key FROM jobs WHERE station_id=? AND state IN ('queued','review_required','metadata_blocked')", (key,)).fetchall():
                db.execute("UPDATE jobs SET state='superseded',updated=? WHERE work_key=?", (time.time(), row[0]))
                event(db, row[0], "station_superseded", {"station_sha256": signature})
        db.execute("INSERT INTO stations(station_id,config,digest) VALUES (?,?,?) "
                   "ON CONFLICT(station_id) DO UPDATE SET config=excluded.config,digest=excluded.digest",
                   (key, encode(data).decode(), signature))
        event(db, None, "station_registered", {"station_id": key, "digest": signature})
    return signature


def get_station(db, station_id):
    row = db.execute("SELECT * FROM stations WHERE station_id=?", (station_id,)).fetchone()
    require(row is not None, "站點未登記")
    data = decode(row["config"])
    require(station_api.station_digest(data) == row["digest"], "站點資料摘要不符")
    return data, row["health"]


def schedule(db, station_id):
    station, _ = get_station(db, station_id)
    created = 0
    with transaction(db):
        for row in db.execute("SELECT * FROM images ORDER BY image_id").fetchall():
            image = decode(row["body"])
            if image["board"] not in station["compatible_boards"]:
                continue
            metadata_ready = not image.get("issues") and bool(re.fullmatch(r"[0-9a-f]{64}", image.get("expected_sha256") or ""))
            binding = {"station_sha256": station_api.station_digest(station),
                       "hardware_id": station["hardware_id"], "image_sha256": image.get("expected_sha256"),
                       "source_metadata_sha256": digest(image), "image_root": row["root"],
                       "boot_config_sha256": station["boot_config_sha256"],
                       "test_version": station["test_version"], "mode": station["mode"]}
            key = digest(binding)
            priority = ":".join((str({"arm32": 0, "arm64": 1, "riscv64": 2}.get(image.get("architecture"), 3)),
                                 image.get("release") or "unknown", image["board"], image.get("variant") or "unknown", key))
            body = {**binding, "image": image, "work_key": key, "station_id": station_id,
                    "station": station}
            now = time.time()
            state = ("metadata_blocked" if not metadata_ready else
                     "review_required" if history_matches(db, station, image) else "queued")
            cursor = db.execute("INSERT OR IGNORE INTO jobs(work_key,station_id,mode,state,body,priority,created,updated) "
                                "VALUES (?,?,?,?,?,?,?,?)",
                                (key, station_id, station["mode"], state, encode(body).decode(), priority, now, now))
            created += cursor.rowcount
        event(db, None, "scheduled", {"station_id": station_id, "created": created})
    return created


def history_matches(db, station, image):
    if station["mode"] != "hardware":
        return False
    for row in db.execute("SELECT body FROM history_refs WHERE image_sha256=?", (image.get("expected_sha256"),)):
        previous = decode(row[0])
        ids = {item.get("hardware_id") for stage in previous["stages"].values() for item in stage["history"]}
        if previous["board"] == image["artifact_board"] and (not station["enabled"] or station["hardware_id"] in ids):
            return True
    return False


def import_history(db, path, expected_sha256):
    blob = read_blob(path)
    require(hashlib.sha256(blob).hexdigest() == expected_sha256, "歷史摘要與指定 SHA-256 不符")
    data = decode(blob)
    require(data.get("schema") == "bpi-h618-lab-summary-v1" and data.get("historical_only") is True and
            data.get("can_skip_current_media") is False and data.get("all_tests_passed") is False,
            "只接受不升格為目前硬體通過的 H618 歷史摘要")
    require(isinstance(data.get("images"), list) and len(data["images"]) <= 2048, "歷史映像數量無效")
    with transaction(db):
        for item in data["images"]:
            sha = item["image"]["compressed"]["sha256"]
            require(isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{64}", sha), "歷史映像摘要無效")
            require(isinstance(item.get("board"), str) and isinstance(item.get("stages"), dict), "歷史身分或階段無效")
            for stage in item["stages"].values():
                require(isinstance(stage, dict) and isinstance(stage.get("history"), list) and
                        all(isinstance(record, dict) for record in stage["history"]), "歷史事件格式無效")
            db.execute("INSERT OR IGNORE INTO history_refs VALUES (?,?,?,?)",
                       (sha, expected_sha256, str(Path(path).absolute()), encode(item).decode()))
        for row in db.execute("SELECT work_key,station_id,body FROM jobs WHERE state='queued'").fetchall():
            body = decode(row["body"])
            station, _ = get_station(db, row["station_id"])
            if history_matches(db, station, body["image"]):
                db.execute("UPDATE jobs SET state='review_required' WHERE work_key=?", (row["work_key"],))
        event(db, None, "history_referenced", {"sha256": expected_sha256, "images": len(data["images"]),
                                              "can_skip_current_media": False})
    return len(data["images"])


def release_review(db, key, reason):
    require(isinstance(reason, str) and reason.strip(), "須說明既有證據、缺測或條件變更，才能釋出工作")
    with transaction(db):
        require(get_job(db, key)["state"] == "review_required", "工作不是歷史證據待審狀態")
        db.execute("UPDATE jobs SET state='queued',updated=? WHERE work_key=?", (time.time(), key))
        event(db, key, "history_review_released", {"reason": reason})


@contextmanager
def station_locks(station, root=LOCK_ROOT):
    root = safe_directory(root, create=True)
    require(root.stat().st_uid == os.geteuid() and root.stat().st_mode & 0o077 == 0,
            "鎖目錄須由目前使用者擁有且不可供其他使用者存取")
    descriptors = []
    try:
        keys = sorted(set(["station:" + station["station_id"], *station_api.resource_keys(station)]))
        for key in keys:
            name = hashlib.sha256(key.encode()).hexdigest() + ".lock"
            fd = os.open(root/name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            descriptors.append(fd)
            require(stat.S_ISREG(os.fstat(fd).st_mode), "鎖檔不是一般檔案")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError("站點或 UART／電源／媒體已由其他工作占用") from exc
        yield
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def get_job(db, key):
    row = db.execute("SELECT * FROM jobs WHERE work_key=?", (key,)).fetchone()
    require(row is not None, "工作不存在")
    job = dict(row)
    body = decode(job["body"])
    require(digest({field: body[field] for field in BINDING_FIELDS}) == body["work_key"] == key and
            job["station_id"] == body["station_id"] and job["mode"] == body["mode"] and
            body["station_sha256"] == station_api.station_digest(body["station"]) and
            body["source_metadata_sha256"] == digest(body["image"]) and
            body["image_sha256"] == body["image"].get("expected_sha256"), "工作內容、來源或模式與固定工作鍵不符")
    return job


def resource_owner(db):
    return str(Path(db.execute("PRAGMA database_list").fetchone()[2]).absolute())


@contextmanager
def reservation_store(root):
    path = safe_directory(root)/"reservations.sqlite3"
    if path.exists() or path.is_symlink():
        require(stat.S_ISREG(path.lstat().st_mode), "資源占用資料庫不是一般檔案")
    store = sqlite3.connect(path, timeout=10, isolation_level=None)
    try:
        store.execute("PRAGMA synchronous=FULL")
        store.execute("CREATE TABLE IF NOT EXISTS reservations (resource TEXT PRIMARY KEY, owner TEXT, work_key TEXT)")
        with transaction(store):
            yield store
    finally:
        store.close()


def reserve_resources(db, station, key, root):
    owner = resource_owner(db)
    with reservation_store(root) as store:
        for resource in station_api.resource_keys(station):
            previous = store.execute("SELECT owner,work_key FROM reservations WHERE resource=?", (resource,)).fetchone()
            require(previous is None or previous == (owner, key),
                    "資源有未釋放的持久占用；先核對原工作並返回救援或 unlock")
            store.execute("INSERT OR IGNORE INTO reservations VALUES (?,?,?)", (resource, owner, key))


def release_resources(db, key, root):
    require(get_job(db, key)["state"] in ("collected", "failed", "blocked", "interrupted_recovered"),
            "未安全結束的工作不能釋放持久占用")
    with reservation_store(root) as store:
        store.execute("DELETE FROM reservations WHERE owner=? AND work_key=?", (resource_owner(db), key))


def unlock_completed(db, key, root=LOCK_ROOT):
    job = get_job(db, key)
    station = decode(job["body"])["station"]
    with station_locks(station, root):
        refs(db, job)
        release_resources(db, key, root)
        with transaction(db):
            event(db, key, "resources_unlocked", {"scope": "只釋放已安全結束工作，不操作設備"})


def refs(db, job):
    found = []
    for row in db.execute("SELECT * FROM reports WHERE work_key=? AND attempt_id=? ORDER BY report_id",
                          (job["work_key"], job["attempt_id"])):
        blob = read_blob(row["path"])
        decode(blob)
        require(hashlib.sha256(blob).hexdigest() == row["sha256"], "既有證據遭更換，禁止續作")
        found.append({"stage": row["stage"], "path": row["path"], "sha256": row["sha256"]})
    return found


def start(db, station, key=None, resume=False, simulation_only=False):
    with transaction(db):
        current, health = get_station(db, station["station_id"])
        require(health == "ready", "站點已隔離，必須先修復救援")
        require(station_api.station_digest(current) == station_api.station_digest(station), "領取期間站點已更換")
        if simulation_only:
            require(current["mode"] == "simulation" and current["adapter"]["kind"] == "simulator-v1",
                    "模擬命令只能領取內建模擬器工作")
        if key is None:
            row = db.execute("SELECT work_key FROM jobs WHERE station_id=? AND state='queued' "
                             "ORDER BY priority LIMIT 1", (station["station_id"],)).fetchone()
            if row is None:
                return None
            key = row[0]
        job = get_job(db, key)
        require(job["station_id"] == station["station_id"], "工作站點不符")
        body = decode(job["body"])
        require(body["station_sha256"] == station_api.station_digest(station), "站點條件已改變，須重新排程")
        other = db.execute("SELECT 1 FROM jobs WHERE station_id=? AND work_key<>? "
                           "AND state IN ('running','interrupted')", (station["station_id"], key)).fetchone()
        require(other is None, "站點有中斷工作，先續作或返回救援")
        require(job["state"] in (("running", "interrupted") if resume else ("queued",)), "目前狀態不能領取")
        if not resume:
            attempt = uuid.uuid4().hex
            db.execute("INSERT INTO attempts VALUES (?,?,?,NULL,NULL)", (attempt, key, time.time()))
            db.execute("UPDATE jobs SET attempt_id=?,cursor=0,inflight=NULL WHERE work_key=?", (attempt, key))
            db.execute("DELETE FROM failure_branches WHERE work_key=?", (key,))
        db.execute("UPDATE jobs SET state='running',updated=? WHERE work_key=?", (time.time(), key))
        event(db, key, "resumed" if resume else "claimed", {})
        return get_job(db, key)


def finish(db, key, state):
    with transaction(db):
        job = get_job(db, key)
        db.execute("UPDATE jobs SET state=?,inflight=NULL,updated=? WHERE work_key=?", (state, time.time(), key))
        db.execute("UPDATE attempts SET finished=?,result=? WHERE attempt_id=?", (time.time(), state, job["attempt_id"]))
        event(db, key, "finished", {"state": state})


def request_for(job, stage, station):
    body = decode(job["body"])
    return {"schema": "bpi-lab-request-v1", "work_key": job["work_key"], "attempt_id": job["attempt_id"],
               "stage": stage, "station_id": station["station_id"], "hardware_id": station["hardware_id"],
               "image_sha256": body["image_sha256"], "boot_config_sha256": body["boot_config_sha256"],
               "test_version": body["test_version"], "mode": body["mode"],
               "image": body["image"], "image_root": body["image_root"]}


def _record_failure(db, key, request, status, needs_recovery=False):
    """由呼叫端的交易保存失敗分支；救援決策必須早於收據發布。"""
    db.execute("INSERT OR REPLACE INTO failure_branches VALUES (?,?,?)", (key, request["stage"], status))
    if needs_recovery:
        event(db, key, "recovery_required", {"attempt_id": request["attempt_id"], "stage": request["stage"]})


def _needs_recovery(db, key, stage):
    if stage != "preflight":
        return True
    attempt = get_job(db, key)["attempt_id"]
    return any(decode(row[0]) == {"attempt_id": attempt, "stage": stage} for row in db.execute(
        "SELECT body FROM events WHERE work_key=? AND kind='recovery_required'", (key,)))


def record_stage(db, key, request, reference, report):
    stage = request["stage"]
    job = get_job(db, key)
    station, _ = get_station(db, job["station_id"])
    expected = request_for(job, stage, station)
    require(all(request.get(field) == expected[field] for field in expected), "階段意圖與目前工作不符")
    station_api.validate_report(report, request)
    is_guard = "resume" in request
    with transaction(db):
        db.execute("INSERT INTO reports(work_key,attempt_id,stage,status,path,sha256,is_resume_guard,created) "
                   "VALUES (?,?,?,?,?,?,?,?)", (key, job["attempt_id"], stage, report["status"],
                                               reference["path"], reference["sha256"], int(is_guard), time.time()))
        passed = report["status"] == "passed"
        cursor = job["cursor"] if is_guard or not passed else STAGES.index(stage) + 1
        if not passed:
            _record_failure(db, key, request, report["status"], is_guard or report.get("needs_recovery", False))
        db.execute("UPDATE jobs SET cursor=?,inflight=NULL,updated=? WHERE work_key=?", (cursor, time.time(), key))
        event(db, key, "stage_recorded", {"stage": stage, "status": report["status"], **reference})


def reconcile_inflight(db, key):
    job = get_job(db, key)
    if job["inflight"] is None:
        return
    row = db.execute("SELECT body FROM events WHERE work_key=? AND kind='stage_started' ORDER BY event_id DESC LIMIT 1", (key,)).fetchone()
    require(row is not None, "缺少執行意圖，禁止猜測中斷階段")
    intent = decode(row[0])
    request = intent["request"]
    require(request["attempt_id"] == job["attempt_id"] and request["stage"] == job["inflight"], "中斷意圖不符")
    path = Path(intent["evidence_directory"])/"queue-receipt.json"
    if path.exists() or path.is_symlink():
        blob = read_blob(path)
        report = decode(blob)
        record_stage(db, key, request, {"path": str(path), "sha256": hashlib.sha256(blob).hexdigest()}, report)
    else:
        require(job["inflight"] != "deploy", "部署結果未知且沒有完整收據；先 recover，再明示 retry，不自動重刷")


def assert_complete(db, key):
    job = get_job(db, key)
    station = decode(job["body"])["station"]
    require(db.execute("SELECT 1 FROM failure_branches WHERE work_key=?", (key,)).fetchone() is None,
            "本次嘗試仍有失敗分支，不能當成收集通過")
    found = set()
    for row in db.execute("SELECT * FROM reports WHERE work_key=? AND attempt_id=? AND is_resume_guard=0",
                          (key, job["attempt_id"])):
        blob = read_blob(row["path"])
        require(hashlib.sha256(blob).hexdigest() == row["sha256"], "階段收據摘要不符")
        report = decode(blob)
        station_api.validate_report(report, request_for(job, row["stage"], station))
        require(report["status"] == row["status"] == "passed", "必要階段未通過")
        found.add(row["stage"])
    require(found == set(STAGES), "必要階段收據不完整")


def execute_stage(db, station, key, stage, output, runner, resume_stage=None):
    job = get_job(db, key)
    request = request_for(job, stage, station)
    if resume_stage is not None:
        request["resume"] = {"next_stage": resume_stage, "previous_reports": refs(db, job)}
    directory = Path(output)/key/job["attempt_id"]/(stage + "-" + uuid.uuid4().hex)
    safe_directory(directory.parent, create=True)
    with transaction(db):
        db.execute("UPDATE jobs SET inflight=?,updated=? WHERE work_key=?", (stage, time.time(), key))
        event(db, key, "stage_started", {"stage": stage, "request": request, "resume_guard": resume_stage is not None,
                                        "evidence_directory": str(directory)})
    try:
        report = runner(station, request, directory)
    except station_api.StageExecutionError as exc:
        needs_recovery = resume_stage is not None or exc.adapter_started and exc.report is None
        if exc.report is not None:
            station_api.validate_report(exc.report, request)
            needs_recovery |= (exc.report.get("needs_recovery", False)
                               or exc.report["status"] == "passed" and "needs_recovery" in exc.report)
        if needs_recovery:
            with transaction(db):
                _record_failure(db, key, request, "failed", needs_recovery=True)
        raise
    station_api.validate_report(report, request)
    if report["status"] != "passed" and (resume_stage is not None or report.get("needs_recovery", False)):
        with transaction(db):
            _record_failure(db, key, request, report["status"], needs_recovery=True)
    try:
        reference = save_json(directory/"queue-receipt.json", report)
        record_stage(db, key, request, reference, report)
    except BaseException:
        if report["status"] == "passed" and "needs_recovery" in report:
            with transaction(db):
                _record_failure(db, key, request, "failed", needs_recovery=True)
        raise
    return report


def note_error(db, key, stage, exc, failure=False):
    with transaction(db):
        event(db, key, "stage_exception", {"stage": stage, "error_type": type(exc).__name__,
                                          "message": str(exc)[:1000]})
        if failure:
            db.execute("INSERT OR REPLACE INTO failure_branches VALUES (?,?,?)", (key, stage, "failed"))


def return_to_rescue(db, station, key, output, runner):
    try:
        job = get_job(db, key)
        if job["inflight"] == "recovery":
            reconcile_inflight(db, key)
            job = get_job(db, key)
        if job["cursor"] == len(STAGES) and job["inflight"] is None:
            references = refs(db, job)
            require(references and references[-1]["stage"] == "recovery", "缺少最終救援收據")
            report = read_json(references[-1]["path"])
            station_api.validate_report(report, request_for(job, "recovery", station))
            require(report["status"] == "passed", "最終救援收據未通過")
            return True
        report = execute_stage(db, station, key, "recovery", output, runner)
        require(report["status"] == "passed", "返回救援未通過")
        return True
    except Exception as exc:
        note_error(db, key, "recovery", exc)
        with transaction(db):
            db.execute("UPDATE stations SET health='quarantined' WHERE station_id=?", (station["station_id"],))
        finish(db, key, "recovery_failed")
        return False


def run_one(db, station_id, output, *, key=None, resume=False, runner=None, lock_root=LOCK_ROOT,
            simulation_only=False):
    station, _ = get_station(db, station_id)
    station_api.validate_station(station)
    require(station["enabled"], "站點停用，不執行工作")
    runner = runner or station_api.run_stage
    safe_directory(output, create=True)
    with station_locks(station, lock_root):
        job = start(db, station, key, resume, simulation_only)
        if job is None:
            return None
        key = job["work_key"]
        try:
            reserve_resources(db, station, key, lock_root)
        except Exception as exc:
            note_error(db, key, "resource_claim", exc)
            if not resume:
                finish(db, key, "blocked")
            raise
        if resume:
            try:
                recovery_only = db.execute("SELECT 1 FROM failure_branches WHERE work_key=? AND state='interrupted_recovered'", (key,)).fetchone()
                if not recovery_only or get_job(db, key)["inflight"] == "recovery":
                    reconcile_inflight(db, key)
                refs(db, get_job(db, key))
            except Exception as exc:
                note_error(db, key, "reconcile", exc)
                with transaction(db):
                    db.execute("UPDATE jobs SET state='interrupted' WHERE work_key=?", (key,))
                return get_job(db, key)
            job = get_job(db, key)
            failure = db.execute("SELECT stage,state FROM failure_branches WHERE work_key=?", (key,)).fetchone()
            if failure:
                if not _needs_recovery(db, key, failure["stage"]) or return_to_rescue(db, station, key, output, runner):
                    finish(db, key, failure["state"])
                    release_resources(db, key, lock_root)
                return get_job(db, key)
            if job["cursor"] == len(STAGES):
                assert_complete(db, key)
                finish(db, key, "collected")
                release_resources(db, key, lock_root)
                return get_job(db, key)
            wanted = STAGES[job["cursor"]]
            guard = None
            try:
                guard = execute_stage(db, station, key, "preflight", output, runner, wanted)
                require(guard["status"] == "passed" and guard.get("resume_state_verified") is True and
                        guard.get("resume_next_stage") == wanted, "現況無法支持續作，未執行後續階段")
            except Exception as exc:
                note_error(db, key, "resume", exc)
                failure_state = "blocked" if guard is not None and guard["status"] == "blocked" else "failed"
                with transaction(db):
                    _record_failure(db, key, request_for(job, "preflight", station), failure_state, needs_recovery=True)
                if return_to_rescue(db, station, key, output, runner):
                    finish(db, key, failure_state)
                    release_resources(db, key, lock_root)
                return get_job(db, key)
        for index in range(job["cursor"], len(STAGES)):
            stage = STAGES[index]
            try:
                result = execute_stage(db, station, key, stage, output, runner)
                failed = result["status"] != "passed"
                failure_state = "blocked" if result["status"] == "blocked" else "failed"
            except Exception as exc:
                note_error(db, key, stage, exc, failure=True)
                failed, failure_state = True, "failed"
            if failed:
                if stage == "recovery":
                    with transaction(db):
                        db.execute("UPDATE stations SET health='quarantined' WHERE station_id=?", (station_id,))
                    finish(db, key, "recovery_failed")
                elif not _needs_recovery(db, key, stage) or return_to_rescue(db, station, key, output, runner):
                    finish(db, key, failure_state)
                    release_resources(db, key, lock_root)
                return get_job(db, key)
        assert_complete(db, key)
        finish(db, key, "collected")
        release_resources(db, key, lock_root)
        return get_job(db, key)


def recover(db, key, output, *, runner=None, lock_root=LOCK_ROOT):
    job = get_job(db, key)
    station, _ = get_station(db, job["station_id"])
    require(station["enabled"] and job["state"] in ("running", "interrupted", "recovery_failed"),
            "只有未結束或救援失敗工作可執行返回救援")
    require(decode(job["body"])["station_sha256"] == station_api.station_digest(station), "站點設定已更換")
    with station_locks(station, lock_root):
        reserve_resources(db, station, key, lock_root)
        # 救援意圖先落盤；即使救援再次中斷，也不能回到部署分支。
        with transaction(db):
            db.execute("INSERT OR IGNORE INTO failure_branches VALUES (?,'recovery','interrupted_recovered')", (key,))
            event(db, key, "recovery_only_requested", {})
        if return_to_rescue(db, station, key, output, runner or station_api.run_stage):
            with transaction(db):
                db.execute("UPDATE stations SET health='ready' WHERE station_id=?", (station["station_id"],))
            finish(db, key, "interrupted_recovered")
            release_resources(db, key, lock_root)
    return get_job(db, key)


def retry(db, key, reason):
    require(isinstance(reason, str) and reason.strip(), "重試須填寫原因")
    with transaction(db):
        job = get_job(db, key)
        require(job["state"] in ("failed", "blocked", "interrupted_recovered"), "不可重試執行中或已收集工作")
        require(get_station(db, job["station_id"])[1] == "ready", "站點尚未恢復")
        db.execute("UPDATE jobs SET state='queued',cursor=0,inflight=NULL,updated=? WHERE work_key=?", (time.time(), key))
        event(db, key, "retry_requested", {"reason": reason})


def summary(db):
    inventory = [decode(r[0]) for r in db.execute("SELECT body FROM images")]
    counts = {"simulation": {}, "hardware": {}}
    for row in db.execute("SELECT work_key FROM jobs"):
        job = get_job(db, row[0])
        if job["state"] == "collected":
            assert_complete(db, row[0])
        counts[job["mode"]][job["state"]] = counts[job["mode"]].get(job["state"], 0) + 1
    return {"schema": "bpi-lab-queue-summary-v1", "inventory_images": len(inventory),
            "inventory_boards": len({i["board"] for i in inventory}),
            "inventory_with_issues": sum(bool(i.get("issues")) for i in inventory),
            "jobs": counts, "events": db.execute("SELECT count(*) FROM events").fetchone()[0],
            "attempts": db.execute("SELECT count(*) FROM attempts").fetchone()[0],
            "reports": db.execute("SELECT count(*) FROM reports").fetchone()[0],
            "historical_images_referenced": db.execute("SELECT count(DISTINCT image_sha256) FROM history_refs").fetchone()[0],
            "historical_nonpassing_reports": db.execute("SELECT count(*) FROM reports WHERE status<>'passed'").fetchone()[0],
            "historical_execution_errors": db.execute("SELECT count(*) FROM events WHERE kind='stage_exception'").fetchone()[0],
            "disabled_stations": [r["station_id"] for r in db.execute("SELECT station_id,config FROM stations")
                                  if not decode(r["config"])["enabled"]],
            "quarantined_stations": [r[0] for r in db.execute("SELECT station_id FROM stations WHERE health<>'ready'")],
            "all_hardware_tests_passed": False,
            "scope": "盤點、模擬與硬體基本階段分列；不是完整功能或長期穩定性認證"}
