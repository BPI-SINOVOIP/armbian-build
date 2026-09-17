#!/usr/bin/env python3
"""以獨立授權執行單板首輪，不借用尚未取得的佇列資格。"""

from contextlib import ExitStack, nullcontext
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import time

if __package__:
    from . import bpi_lab_backend as backend
    # 佇列仍使用平面匯入；共用同一站點驗證器，不改動佇列模組。
    sys.modules.setdefault("bpi_lab_station", backend.station)
    from . import bpi_lab_queue as queue
else:
    import bpi_lab_backend as backend
    import bpi_lab_queue as queue

deploy, station, require = backend.deploy, backend.station, backend.require


def reference(path, value):
    return {"path": str(path), "sha256": hashlib.sha256(deploy.encode(value)).hexdigest()}


def inputs(config_ref, authorization_ref, request):
    station._validate_request(request)
    require(request["mode"] == "hardware" and request["stage"] == "preflight"
            and "resume" not in request, "首次核定須以獨立硬體預檢請求開始")
    config, contract = backend.load_inputs(config_ref)
    require(request["boot_config_sha256"] == config_ref["sha256"], "請求未綁定完整設定")
    for key in ("station_id", "hardware_id", "test_version"):
        require(config[key] == request[key], "首次核定工作身分不符：" + key)
    auth = deploy.load(authorization_ref)
    deploy.fields(auth, "schema approved record scope_sha256 image_sha256 stages allow_failure_recovery "
                  "driver_sha256 queue_sha256")
    require(auth["schema"] == "bpi-lab-first-cycle-authorization-v1" and auth["approved"] is True,
            "缺少首次單板試驗授權")
    deploy.identifier(auth["record"])
    require(auth["scope_sha256"] == backend.scope_digest(config)
            and auth["image_sha256"] == request["image_sha256"]
            and auth["stages"] == list(station.STAGES)
            and type(auth["allow_failure_recovery"]) is bool,
            "首次授權未綁定本設定、單一映像及完整階段")
    deploy.checked_bytes({"path": str(Path(__file__).absolute()), "sha256": auth["driver_sha256"]})
    check_queue(auth)
    require(request["attempt_id"] == auth["record"], "首次試驗編號必須等同授權紀錄")
    wanted = hashlib.sha256(deploy.encode({"scope": auth["scope_sha256"],
                                         "authorization": authorization_ref["sha256"]})).hexdigest()
    require(request["work_key"] == wanted, "首次核定工作鍵未綁定授權")
    bundle, boot, expected = backend.selected_image(config, contract, request)
    return config, contract, bundle, boot, expected, auth


def check_queue(auth):
    deploy.checked_bytes({"path": str(Path(queue.__file__).absolute()), "sha256": auth["queue_sha256"]})


def isolation_station(config, request):
    """僅供資源鎖鍵使用；不建立已核定或啟用的站點。"""
    return {"schema": "bpi-lab-station-v1", "mode": "hardware", "enabled": False,
            **{key: config[key] for key in ("station_id", "hardware_id", "test_version", "timeout_seconds", "resources")},
            "board": request["image"]["board"], "compatible_boards": [request["image"]["board"]]}


def reservation_status(db, keys, owner, key):
    rows = [db.execute("SELECT owner,work_key FROM reservations WHERE resource=?", (item,)).fetchone()
            for item in keys]
    require(all(row is None or row == (owner, key) for row in rows), "資源有其他工作持久占用；不得越過隔離")
    require(not any(rows) or all(rows), "首次核定持久占用不完整；保持隔離")
    return all(rows)


def release_reservation(keys, owner, key):
    with queue.reservation_store(queue.LOCK_ROOT) as db:
        require(reservation_status(db, keys, owner, key), "首次核定持久占用遺失；不能宣告釋放")
        db.execute("DELETE FROM reservations WHERE owner=? AND work_key=?", (owner, key))


def capture_collection(stage, result, output):
    if stage not in ("boot", "smoke"):
        return result
    path = output / ("cycle/linux/collection.json" if stage == "boot" else "linux/collection.json")
    with deploy.safe.open_root(path.parent) as root:
        digest, _ = deploy.safe.fingerprint(root, path.name, limit=16 * 1024**2)
    ref = {"path": str(path), "sha256": digest["sha256"]}
    if "linux_collection" in result:
        require(result["linux_collection"] == ref, "原生 Linux 採樣參照與本階段固定檔案不符")
        return result
    return {**result, "linux_collection": ref}


def validate_operation(stage, result, request, contract, bundle, config, *, current=None):
    backend.validate_result(stage, result, request, contract, bundle, config=config, current=current)


def validate_boot_sequence(stage, boot_id, previous_ids):
    if previous_ids:
        if stage in ("boot", "recovery"):
            require(boot_id not in previous_ids, "冷循環沒有新的 boot_id")
        else:
            require(boot_id == previous_ids[-1], "觀測階段之間發生未授權重啟")


def stage_report(request, result, operation, previous_report, *, simulated):
    report = {"schema": "bpi-lab-stage-v1", **{key: request[key] for key in station.BINDINGS},
              "status": "blocked" if simulated else "passed", "hardware_validated": not simulated,
              "whole_backend_ready": False, "original_boot_chain_verified": False,
              "qualification_pending": True, "operation": operation, "previous_report": previous_report}
    flags = {"preflight": ("media_identity_verified", "backup_verified"),
             "deploy": ("full_readback_verified", "compressed_sha256_verified"),
             "boot": ("customer_kernel_verified",), "recovery": ("rescue_verified",)}
    if request["stage"] == "smoke":
        report.update(checks={item["check"]: True for item in result["checks"]},
                      smoke_scope="linux-read-only", stress_tested=False)
    else:
        report.update({key: True for key in flags[request["stage"]]})
    if simulated:
        report.update(test_only=True, reason="替身不產生硬體或核定資格")
    station.validate_report(report, request)
    return report


def verified_state(current, request, contract, bundle, config):
    prior = {**request, "stage": current["stage"]}
    result, report = deploy.load(current["operation"]), deploy.load(current["report"])
    validate_operation(current["stage"], result, prior, contract, bundle, config)
    station.validate_report(report, prior)
    require(report == stage_report(prior, result, current["operation"], report.get("previous_report"), simulated=False),
            "持久完成狀態缺少一致的操作及報告")
    require(current["boot_id"] == result["session"]["boot_id"] and current["phase"] == result["session"]["mode"],
            "持久完成狀態與操作身分不符")
    return result["session"]


def publication_record(summary):
    content = {key: value for key, value in summary.items() if key != "publication"}
    return {"schema": "bpi-lab-first-cycle-publication-v1",
            "summary_sha256": hashlib.sha256(deploy.encode(content)).hexdigest()}


def remove_publication_file(output, name):
    with deploy.safe.open_root(output) as root:
        try:
            os.unlink(name, dir_fd=root)
        except FileNotFoundError:
            pass
        os.fsync(root)


def publish_summary(summary, output, deadline, keys, owner, *, synthetic):
    try:
        deploy.core.deadline_check(deadline, time.monotonic)
        seal = publication_record(summary)
        summary["publication"] = reference(output / "publication.json", seal)
        deploy.save(output, "result.json", deploy.encode(summary))
        deploy.core.deadline_check(deadline, time.monotonic)
        deploy.save(output, "publication.json", deploy.encode(seal))
        deploy.core.deadline_check(deadline, time.monotonic)
        remove_publication_file(output, "publication.pending")
        deploy.core.deadline_check(deadline, time.monotonic)
    except BaseException as exc:
        # 摘要可能已落盤；撤銷完成憑證，不能只回傳失敗卻留下可核定檔案。
        remove_publication_file(output, "publication.json")
        try:
            deploy.save(output, "publication.pending", deploy.encode({"work_key": summary["request"]["work_key"]}))
        except FileExistsError:
            pass
        failed = {**summary, "status": "failed", "hardware_validated": False,
                  "qualification_ready_for_review": False, "failed_stage": "publication",
                  "reason": "最終發布未在期限內完整完成；保持隔離，不產生核定資格", "error_type": type(exc).__name__}
        deploy.save(output, "publication-failed.json", deploy.encode(failed))
        if not isinstance(exc, Exception):
            raise
        return failed
    # 所有完成判斷均在租約內；釋放後不得撤銷發布或再執行決定完成與否的步驟。
    if not synthetic:
        release_reservation(keys, owner, summary["request"]["work_key"])
    return summary


def validate_publication(candidate, config, auth):
    output = deploy.path(config["output_root"]) / ("first-cycle-" + auth["record"])
    ref = candidate.get("publication")
    require(type(ref) is dict and ref.get("path") == str(output / "publication.json"), "缺少原首輪發布完成憑證")
    require(deploy.load(ref) == publication_record(candidate), "發布完成憑證未綁定本摘要")
    with deploy.safe.open_root(output) as root:
        for name in ("publication.pending", "publication-failed.json"):
            try:
                os.stat(name, dir_fd=root, follow_symlinks=False)
            except FileNotFoundError:
                continue
            require(False, "首輪發布仍未完成或曾失敗，不能核定")


def first_cycle(config_ref, authorization_ref, request, *, execute=False, recover=False, runtime=None):
    config, contract, bundle, boot, expected, auth = inputs(config_ref, authorization_ref, request)
    summary = {"schema": "bpi-lab-first-cycle-v1", "status": "checked", "hardware_validated": False,
               "qualification_ready_for_review": False, "whole_backend_ready": False,
               "config": config_ref, "authorization": authorization_ref,
               "scope_sha256": backend.scope_digest(config), "request": request,
               "reports": [], "operations": [], "original_boot_chain_verified": False}
    if not execute:
        require(not recover, "復原須明示執行；離線核對不改變實板")
        return summary
    require(not recover or auth["allow_failure_recovery"], "首次授權不允許失敗復原")
    deadline = time.monotonic() + config["timeout_seconds"]
    synthetic = runtime is not None
    runner = runtime if runtime is not None else backend.NativeRuntime()
    output = deploy.path(config["output_root"]) / ("first-cycle-" + auth["record"])
    owner = str(output / "intent.json")
    if recover:
        output = output.with_name(output.name + "-recovery-" + secrets.token_hex(8))
    binding = {key: request[key] for key in backend.session.BINDINGS}
    current, store = None, backend.StateStore(config)
    steps = ("recovery",) if recover else station.STAGES
    isolation = isolation_station(config, request)
    keys = queue.station_api.resource_keys(isolation)
    with ExitStack() as locks:
        if not synthetic:
            locks.enter_context(queue.station_locks(isolation, queue.LOCK_ROOT))
            locks.enter_context(backend.resource_lock(config))
            current = store.read()
            if current is None:
                with deploy.safe.open_root(backend.LOCK_ROOT) as root:
                    try:
                        deploy.safe.fingerprint(root, store.pending, limit=station.MAX_OUTPUT_BYTES)
                    except FileNotFoundError:
                        pass
                    else:
                        require(False, "持久狀態遺失但仍有 pending；保持隔離，不推定 writer 已停止")
        with nullcontext(None) if synthetic else queue.reservation_store(queue.LOCK_ROOT) as db:
            owned = False if synthetic else reservation_status(db, keys, owner, request["work_key"])
            safe_previous = current is None or (current["status"] == "verified" and current["stage"] == "recovery"
                                                and current["phase"] == "rescue" and not current.get("writer_unresolved")
                                                and current["binding"] != binding)
            if recover:
                # 占用已提交、首個意圖尚未落盤時中斷，尚無任何部署入口可被執行。
                if owned and safe_previous:
                    current = {"schema": "bpi-lab-backend-state-v1", "binding": binding,
                               "status": "failed", "stage": "preflight", "phase": "rescue", "boot_id": None,
                               "first_cycle_authorization": authorization_ref, "writer_unresolved": False}
                require(owned and current is not None and current["binding"] == binding
                        and current["status"] in ("running", "failed", "verified")
                        and current.get("first_cycle_authorization") == authorization_ref,
                        "復原只能接續此授權留下的同板同工作中斷")
                backend.require_recovery_safe(current)
                if current["status"] == "verified":
                    verified_state(current, request, contract, bundle, config)
                    if current["stage"] == "recovery":
                        # 救援已完整落盤，只補發布與釋放，不再次切電。
                        steps = ()
                        summary["reports"].append(current["report"])
                        summary["operations"].append(current["operation"])
            else:
                require(not owned and safe_previous,
                        "前工作尚未返回救援，或此首次試驗已執行；不得重刷")
                current = None
            if not synthetic:
                for key in keys:
                    db.execute("INSERT OR IGNORE INTO reservations VALUES (?,?,?)", (key, owner, request["work_key"]))
        deploy.new_directory(output)
        deploy.save(output, "request.json", deploy.encode(request))
        deploy.save(output, "intent.json", deploy.encode(summary))
        deploy.save(output, "publication.pending", deploy.encode({"work_key": request["work_key"]}))
        boot_ids = [current["boot_id"]] if current and current.get("boot_id") else []
        previous_report = current.get("report") if current else None
        for step in steps:
            stage_request = {**request, "stage": step}
            active = {"schema": "bpi-lab-backend-state-v1", "binding": binding,
                      "status": "running", "stage": step, "phase": current["phase"] if current else "rescue",
                      "boot_id": current.get("boot_id") if current else None, "output": str(output),
                      "first_cycle_authorization": authorization_ref,
                      "writer_unresolved": step == "deploy" or bool(current and current.get("writer_unresolved"))}
            intent_written, published = False, False
            try:
                directory = deploy.new_directory(output / step)
                deploy.save(directory, "request.json", deploy.encode(stage_request))
                if not synthetic:
                    store.write(active)
                    intent_written = True
                backend.check_dependencies(config["dependencies"])
                check_queue(auth)
                # 首次核定仍使用同一原配核對與原生五階段，不提供任意命令鉤子。
                checked = backend.selected_image(config, contract, stage_request)
                require(checked == (bundle, boot, expected), "首次測試途中固定輸入變動")
                remaining = deploy.core.deadline_check(deadline, time.monotonic)
                result = runner.execute((config, contract, bundle, boot, expected, stage_request),
                                        current, directory / "operation", remaining)
                result = capture_collection(step, result, directory / "operation")
                validate_operation(step, result, stage_request, contract, bundle, config, current=current)
                validate_boot_sequence(step, result["session"]["boot_id"], boot_ids)
                backend.check_dependencies(config["dependencies"])
                check_queue(auth)
                deploy.core.deadline_check(deadline, time.monotonic)
                deploy.save(directory, "operation.json", deploy.encode(result))
                deploy.core.deadline_check(deadline, time.monotonic)
                operation = reference(directory / "operation.json", result)
                report = stage_report(stage_request, result, operation, previous_report, simulated=synthetic)
                observed = result["session"]
                completed = {**active, "status": "verified", "phase": observed["mode"],
                             "boot_id": observed["boot_id"], "operation": operation}
                if step == "deploy":
                    completed["writer_unresolved"] = False
                if synthetic:
                    deploy.save(directory, "report.json", deploy.encode(report))
                else:
                    store.publish(completed, directory, report)
                published = True
                completed["report"] = reference(directory / "report.json", report)
                current = completed
                boot_ids.append(observed["boot_id"])
                previous_report = completed["report"]
                summary["reports"].append(completed["report"])
                summary["operations"].append(operation)
                deploy.core.deadline_check(deadline, time.monotonic)
            except BaseException as exc:
                if not synthetic and intent_written and not published:
                    store.write({**active, "status": "failed"})
                summary.update(status="failed", failed_stage=step,
                               reason="階段未完整完成，保留隔離狀態；禁止自動重刷",
                               error_type=type(exc).__name__)
                deploy.save(output, "result.json", deploy.encode(summary))
                if not isinstance(exc, Exception):
                    raise
                return summary
        summary.update(status="test-only" if synthetic else "recovered" if recover else "review-required",
                       hardware_validated=not synthetic,
                       qualification_ready_for_review=not synthetic and not recover,
                       rescue_verified=not synthetic,
                       test_only=synthetic)
        summary = publish_summary(summary, output, deadline, keys, owner, synthetic=synthetic)
    return summary


def approve(candidate_ref, review_ref, output):
    """核對完整首輪及明示人工審閱後產生資格檔；不啟用站點、不操作設備。"""
    candidate, review = deploy.load(candidate_ref), deploy.load(review_ref)
    deploy.fields(review, "schema approved record candidate scope_sha256")
    require(review["schema"] == "bpi-lab-first-cycle-review-v1" and review["approved"] is True
            and review["candidate"] == candidate_ref, "缺少綁定本次首輪證據的明示審閱")
    deploy.identifier(review["record"])
    require(candidate.get("schema") == "bpi-lab-first-cycle-v1"
            and candidate.get("status") == "review-required" and candidate.get("hardware_validated") is True
            and candidate.get("qualification_ready_for_review") is True
            and candidate.get("test_only") is False and candidate.get("rescue_verified") is True,
            "只有原生完整首輪結果可供核定，替身或單獨復原不能升格")
    config, contract, bundle, boot, expected, auth = inputs(
        candidate["config"], candidate["authorization"], candidate["request"])
    validate_publication(candidate, config, auth)
    scope = backend.scope_digest(config)
    require(scope == candidate["scope_sha256"] == review["scope_sha256"], "審閱期間設定變更")
    reports, operations = candidate["reports"], candidate["operations"]
    require(type(reports) is list and type(operations) is list
            and len(reports) == len(operations) == len(station.STAGES), "首輪證據不是完整五階段")
    require(len({item["path"] for item in reports}) == len(reports)
            and len({item["path"] for item in operations}) == len(operations), "五階段證據路徑重複")
    previous_report, boot_ids, current, auxiliary = None, [], None, []
    for stage, report_ref, operation_ref in zip(station.STAGES, reports, operations):
        request = {**candidate["request"], "stage": stage}
        result, report = deploy.load(operation_ref), deploy.load(report_ref)
        validate_operation(stage, result, request, contract, bundle, config, current=current)
        station.validate_report(report, request)
        require(report["status"] == "passed" and report["hardware_validated"] is True
                and report.get("test_only", False) is False, "首輪含未通過或合成階段")
        require(report == stage_report(request, result, operation_ref, previous_report, simulated=False),
                "階段報告未綁定操作內容及有序前一報告")
        validate_boot_sequence(stage, result["session"]["boot_id"], boot_ids)
        boot_ids.append(result["session"]["boot_id"])
        previous_report = report_ref
        current = {"boot_id": result["session"]["boot_id"], "phase": result["session"]["mode"],
                   "operation": operation_ref}
        auxiliary.append(result["session"]["ssh"]["known_hosts"])
        if stage in ("boot", "smoke"):
            auxiliary.append(result["linux_collection"])
    qualification = {"schema": "bpi-lab-backend-qualification-v1", "scope_sha256": scope,
                     "approved_stages": list(station.STAGES), "hardware_validated": True,
                     "rescue_verified": True, "single_image_cycle_verified": True,
                     "source_evidence": [candidate_ref, review_ref, candidate["publication"], *reports, *operations, *auxiliary]}
    output = deploy.path(output)
    deploy.save(output.parent, output.name, deploy.encode(qualification))
    return qualification


def main(argv=None):
    parser = argparse.ArgumentParser(description="首次單板五階段試驗與人工審閱核定")
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("check", "run", "recover"):
        command = sub.add_parser(name)
        for option in ("config", "authorization", "request"):
            command.add_argument("--" + option, required=True, type=Path)
            command.add_argument("--" + option + "-sha256", required=True)
        if name != "check":
            command.add_argument("--confirm-hardware-test", action="store_true", required=True)
    command = sub.add_parser("approve")
    for option in ("candidate", "review"):
        command.add_argument("--" + option, required=True, type=Path)
        command.add_argument("--" + option + "-sha256", required=True)
    command.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    def ref(name):
        return {"path": str(getattr(args, name).absolute()), "sha256": getattr(args, name + "_sha256")}
    try:
        if args.action == "approve":
            result = approve(ref("candidate"), ref("review"), args.output.absolute())
        else:
            result = first_cycle(ref("config"), ref("authorization"), deploy.load(ref("request")),
                                 execute=args.action != "check", recover=args.action == "recover")
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("status") in (None, "checked", "review-required", "recovered") else 2
    except (ValueError, OSError, KeyError, TypeError, queue.sqlite3.Error) as exc:
        print(json.dumps({"status": "blocked", "error_type": type(exc).__name__,
                          "reason": "首次核定契約或證據未通過；不自動改寫設定", "hardware_validated": False}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
