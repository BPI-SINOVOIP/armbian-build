#!/usr/bin/env python3
"""外部媒體首次單套試驗與獨立審閱；不借用尚未完成的批次資格。"""

from contextlib import ExitStack, nullcontext
import argparse
import json
from pathlib import Path
import secrets
import time

if __package__:
    from . import bpi_lab_external_backend as backend
    from . import bpi_lab_qualify as common
else:
    import bpi_lab_external_backend as backend
    import bpi_lab_qualify as common

deploy, station, require = backend.deploy, backend.station, backend.require
queue = common.queue
reference = backend.reference


def inputs(config_ref, authorization_ref, request):
    station._validate_request(request)
    require(request["mode"] == "hardware" and request["stage"] == "preflight" and "resume" not in request,
            "首次外部試驗須從獨立硬體預檢開始")
    config = backend.load_inputs(config_ref)
    require(request["boot_config_sha256"] == config_ref["sha256"] and all(
        request[key] == config[key] for key in ("station_id", "hardware_id", "test_version")), "首次請求未綁定本設定")
    auth = deploy.load(authorization_ref)
    deploy.fields(auth, "schema approved record scope_sha256 image_sha256 stages allow_failure_recovery driver_sha256 queue_sha256")
    require(auth["schema"] == "bpi-lab-external-first-authorization-v1" and auth["approved"] is True,
            "缺少外部媒體首次明示授權")
    deploy.identifier(auth["record"])
    require(auth["scope_sha256"] == backend.scope_digest(config) and auth["image_sha256"] == request["image_sha256"]
            and auth["stages"] == list(station.STAGES) and type(auth["allow_failure_recovery"]) is bool,
            "首次授權未綁定單套原映像與完整五階段")
    deploy.checked_bytes({"path": str(Path(__file__).absolute()), "sha256": auth["driver_sha256"]})
    common.check_queue(auth)
    require(request["attempt_id"] == auth["record"] and request["work_key"] == backend.media.digest(
        {"scope": auth["scope_sha256"], "authorization": authorization_ref["sha256"]}), "首次工作鍵或 attempt 不符")
    return config, *backend.selected_image(config, request), auth


def verified_state(current, request, config, contract, bundle, boot, expected):
    prior = {**request, "stage": current["stage"]}
    result, report = deploy.load(current["operation"]), deploy.load(current["report"])
    backend.validate_result(current["stage"], result, prior, config, contract, bundle, boot, expected)
    require(report == backend.passed_report(prior, result, current["operation"], report.get("previous_report"))
            and current["phase"] == result["session"]["mode"] and current["boot_id"] == result["session"]["boot_id"],
            "持久完成狀態與原始證據不一致")


def first_cycle(config_ref, authorization_ref, request, *, execute=False, recover=False, runtime=None):
    config, contract, bundle, boot, expected, auth = inputs(config_ref, authorization_ref, request)
    summary = {"schema": "bpi-lab-external-first-cycle-v1", "status": "checked", "hardware_validated": False,
               "qualification_ready_for_review": False, "whole_backend_ready": False,
               "original_boot_chain_verified": False, "derived_initramfs": True, "test_only": runtime is not None,
               "config": config_ref, "authorization": authorization_ref, "scope_sha256": backend.scope_digest(config),
               "request": request, "reports": [], "operations": []}
    if not execute:
        require(not recover, "復原須明示執行")
        return summary
    require(not recover or auth["allow_failure_recovery"], "授權不允許失敗復原")
    synthetic = runtime is not None
    runner = backend.NativeRuntime() if runtime is None else runtime
    deadline = time.monotonic() + config["timeout_seconds"]
    output = deploy.path(config["output_root"]) / ("first-cycle-" + auth["record"])
    owner = str(output / "intent.json")
    if recover:
        output = output.with_name(output.name + "-recovery-" + secrets.token_hex(8))
    store = backend.StateStore(config, contract, bundle["image"])
    isolation = common.isolation_station(config, request)
    keys = queue.station_api.resource_keys(isolation)
    binding = {key: request[key] for key in backend.BINDINGS}
    current, steps = None, ("recovery",) if recover else station.STAGES
    with ExitStack() as locks:
        if not synthetic:
            locks.enter_context(queue.station_locks(isolation, queue.LOCK_ROOT))
            locks.enter_context(backend.core.resource_lock(config))
            current = store.read()
        with nullcontext(None) if synthetic else queue.reservation_store(queue.LOCK_ROOT) as db:
            owned = False if synthetic else common.reservation_status(db, keys, owner, request["work_key"])
            safe = current is None or (current["status"] == "verified" and current["stage"] == "recovery"
                and current["phase"] == "rescue" and not current.get("writer_unresolved") and current["binding"] != binding)
            if recover:
                if owned and safe:
                    current = {"schema": "bpi-lab-backend-state-v1", "binding": binding, "status": "failed",
                               "stage": "preflight", "phase": "rescue", "boot_id": None,
                               "first_cycle_authorization": authorization_ref, "writer_unresolved": False}
                require(owned and current is not None and current["binding"] == binding
                        and current.get("first_cycle_authorization") == authorization_ref,
                        "只能接續原授權、原 attempt 與完整持久租約")
                backend.core.require_recovery_safe(current)
                if current["status"] == "verified":
                    verified_state(current, request, config, contract, bundle, boot, expected)
                    if current["stage"] == "recovery":
                        steps = ()
                        summary["reports"].append(current["report"])
                        summary["operations"].append(current["operation"])
            else:
                require(not owned and safe, "已有未完成工作或此首次試驗已執行，不得重刷")
                current = None
            if not synthetic:
                for key in keys:
                    db.execute("INSERT OR IGNORE INTO reservations VALUES (?,?,?)", (key, owner, request["work_key"]))
        # 租約先於任何可中斷的目錄或階段寫入；同授權 recover 才可承接意圖前窗口。
        deploy.new_directory(output)
        deploy.save(output, "request.json", deploy.encode(request))
        deploy.save(output, "intent.json", deploy.encode(summary))
        deploy.save(output, "publication.pending", deploy.encode({"work_key": request["work_key"]}))
        boot_ids = [current["boot_id"]] if current and current.get("boot_id") else []
        previous_report = current.get("report") if current else None
        for stage in steps:
            req = {**request, "stage": stage}
            active = {"schema": "bpi-lab-backend-state-v1", "binding": binding, "status": "running", "stage": stage,
                      "phase": current["phase"] if current else "rescue", "boot_id": current.get("boot_id") if current else None,
                      "output": str(output), "first_cycle_authorization": authorization_ref}
            intent, published = False, False
            try:
                directory = deploy.new_directory(output / stage)
                deploy.save(directory, "request.json", deploy.encode(req))
                if not synthetic:
                    store.write(active)
                    intent = True
                backend.check_dependencies(config)
                common.check_queue(auth)
                require(backend.selected_image(config, req) == (contract, bundle, boot, expected), "階段間固定輸入漂移")
                remaining = deploy.core.deadline_check(deadline, time.monotonic)
                result = runner.execute((config, contract, bundle, boot, expected, req), current, directory / "operation", remaining)
                backend.validate_result(stage, result, req, config, contract, bundle, boot, expected, current=current)
                common.validate_boot_sequence(stage, result["session"]["boot_id"], boot_ids)
                backend.check_dependencies(config)
                common.check_queue(auth)
                deploy.core.deadline_check(deadline, time.monotonic)
                deploy.save(directory, "operation.json", deploy.encode(result))
                operation = reference(directory / "operation.json", result)
                report = backend.passed_report(req, result, operation, previous_report, synthetic=synthetic)
                observed = result["session"]
                completed = {**active, "status": "verified", "phase": observed["mode"], "boot_id": observed["boot_id"],
                             "operation": operation}
                if synthetic:
                    deploy.save(directory, "report.json", deploy.encode(report))
                else:
                    store.publish(completed, directory, report)
                published = True
                completed["report"] = reference(directory / "report.json", report)
                current, previous_report = completed, completed["report"]
                boot_ids.append(observed["boot_id"])
                summary["reports"].append(previous_report)
                summary["operations"].append(operation)
                deploy.core.deadline_check(deadline, time.monotonic)
            except BaseException as exc:
                if not synthetic and intent and not published:
                    store.write({**active, "status": "failed"})
                summary.update(status="failed", failed_stage=stage, needs_recovery=not synthetic,
                               error_type=type(exc).__name__, reason="操作或發布中斷；租約保留，禁止自動重刷")
                deploy.save(output, "result.json", deploy.encode(summary))
                if not isinstance(exc, Exception):
                    raise
                return summary
        summary.update(status="test-only" if synthetic else "recovered" if recover else "review-required",
                       hardware_validated=not synthetic, qualification_ready_for_review=not synthetic and not recover,
                       rescue_verified=not synthetic)
        # 使用已覆驗的 Q5 發布順序；釋放租約後不得再撤銷完成判定。
        return common.publish_summary(summary, output, deadline, keys, owner, synthetic=synthetic)


def validate_candidate(candidate_ref, review_ref):
    candidate, review = deploy.load(candidate_ref), deploy.load(review_ref)
    deploy.fields(review, "schema approved record candidate scope_sha256 approved_images")
    require(review["schema"] == "bpi-lab-external-first-review-v1" and review["approved"] is True
            and review["candidate"] == candidate_ref, "缺少綁定本次證據的人工審閱")
    deploy.identifier(review["record"])
    require(candidate.get("schema") == "bpi-lab-external-first-cycle-v1"
            and candidate.get("status") == "review-required" and candidate.get("hardware_validated") is True
            and candidate.get("qualification_ready_for_review") is True and candidate.get("rescue_verified") is True
            and candidate.get("test_only") is False and candidate.get("derived_initramfs") is True
            and candidate.get("original_boot_chain_verified") is False, "合成、復原或不完整首輪不得核定")
    config, contract, bundle, boot, expected, auth = inputs(candidate["config"], candidate["authorization"], candidate["request"])
    common.validate_publication(candidate, config, auth)
    require(candidate["scope_sha256"] == review["scope_sha256"] == backend.scope_digest(config), "核定配置漂移")
    images = review["approved_images"]
    require(type(images) is list and images and len(images) == len(set(images))
            and candidate["request"]["image_sha256"] in images and all(key in config["images"] for key in images),
            "批次映像範圍須明示且包含本次首輪原映像")
    reports, operations = candidate["reports"], candidate["operations"]
    require(type(reports) is list and type(operations) is list and len(reports) == len(operations) == len(station.STAGES)
            and len({item["path"] for item in reports}) == len(reports)
            and len({item["path"] for item in operations}) == len(operations), "缺少完整且不重複的五階段證據")
    previous, current, boot_ids = None, None, []
    for stage, report_ref, operation_ref in zip(station.STAGES, reports, operations):
        req = {**candidate["request"], "stage": stage}
        operation, report = deploy.load(operation_ref), deploy.load(report_ref)
        backend.validate_result(stage, operation, req, config, contract, bundle, boot, expected, current=current)
        require(report == backend.passed_report(req, operation, operation_ref, previous), "報告未綁定有序操作證據")
        common.validate_boot_sequence(stage, operation["session"]["boot_id"], boot_ids)
        boot_ids.append(operation["session"]["boot_id"])
        previous = report_ref
        current = {"phase": operation["session"]["mode"], "boot_id": boot_ids[-1], "operation": operation_ref}
    return candidate, review, config


def approve(candidate_ref, review_ref, output):
    candidate, review, config = validate_candidate(candidate_ref, review_ref)
    qualification = {"schema": "bpi-lab-external-qualification-v1", "scope_sha256": backend.scope_digest(config),
                     "approved_stages": list(station.STAGES), "approved_images": review["approved_images"],
                     "hardware_validated": True, "single_image_cycle_verified": True, "rescue_verified": True,
                     "original_boot_chain_verified": False, "derived_initramfs": True,
                     "candidate": candidate_ref, "review": review_ref}
    output = deploy.path(output)
    deploy.save(output.parent, output.name, deploy.encode(qualification))
    return qualification


def check_qualification(config):
    qualification = deploy.load(config["qualification"])
    deploy.fields(qualification, "schema scope_sha256 approved_stages approved_images hardware_validated "
                  "single_image_cycle_verified rescue_verified original_boot_chain_verified derived_initramfs candidate review")
    require(qualification["schema"] == "bpi-lab-external-qualification-v1"
            and qualification["scope_sha256"] == backend.scope_digest(config)
            and qualification["approved_stages"] == list(station.STAGES)
            and all(qualification[key] is True for key in
                ("hardware_validated", "single_image_cycle_verified", "rescue_verified", "derived_initramfs"))
            and qualification["original_boot_chain_verified"] is False, "外部後端未取得完整且同配置的核定")
    candidate, review, original = validate_candidate(qualification["candidate"], qualification["review"])
    require(backend.scope_digest(original) == backend.scope_digest(config)
            and qualification["approved_images"] == review["approved_images"], "資格不能移用到其他設定或映像")
    return qualification


def export_station(config_ref, output, interpreter, *, enabled=False):
    """僅匯出經重驗的站點檔，不登記佇列、不執行硬體或自動啟用既有站點。"""
    require(type(enabled) is bool, "站點啟用旗標型別不符")
    config = backend.load_inputs(config_ref)
    qualification = check_qualification(config)
    interpreter = deploy.path(str(interpreter))
    with station._open_regular(interpreter, executable=True) as stream:
        interpreter_sha256 = station._digest_file(stream)
    adapter = {"kind": "external-v1", "argv": [str(interpreter), "-B", str(Path(backend.__file__).absolute()),
               "--config", config_ref["path"], "--config-sha256", config_ref["sha256"]], "sha256": interpreter_sha256}
    bindings = {key: config[key] for key in ("station_id", "hardware_id", "test_version", "resources")}
    bindings["boot_config_sha256"] = config_ref["sha256"]
    envelope = {"schema": "bpi-lab-qualification-v1", **bindings, "media": config["resources"]["media"],
                "adapter_sha256": interpreter_sha256, "hardware_validated": True, "rescue_verified": True,
                "single_image_cycle_verified": True, "source_evidence": [config["qualification"], config_ref]}
    boards = sorted({deploy.load(config["images"][key])["board"] for key in qualification["approved_images"]})
    output = deploy.new_directory(output)
    deploy.save(output, "qualification.json", deploy.encode(envelope))
    proof = reference(output / "qualification.json", envelope)
    candidate = deploy.load(qualification["candidate"])
    _, contract, *_ = inputs(candidate["config"], candidate["authorization"], candidate["request"])
    value = {"schema": "bpi-lab-station-v1", **bindings, "mode": "hardware", "enabled": enabled,
             "board": boards[0], "compatible_boards": boards, "timeout_seconds": min(86400, config["timeout_seconds"] + 60),
             "adapter": adapter, "qualification": {"status": "qualified", "evidence_path": proof["path"],
                                                    "evidence_sha256": proof["sha256"]},
             "authorization": {"userarea_write": True, "hardware_id": config["hardware_id"],
                               "media_identity": config["resources"]["media"], "backup_sha256": contract["backup"]["sha256"],
                               "record": deploy.load(qualification["review"])["record"]}}
    station.validate_station(value)
    station._verify_qualification(value)
    deploy.save(output, "station.json", deploy.encode(value))
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description="外部媒體首次單套試驗、故障復原與審閱核定")
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
    command.add_argument("--output", type=Path, required=True)
    command = sub.add_parser("station")
    command.add_argument("--config", type=Path, required=True)
    command.add_argument("--config-sha256", required=True)
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--interpreter", type=Path, required=True)
    command.add_argument("--enable-reviewed-station", action="store_true")
    args = parser.parse_args(argv)
    def ref(name):
        return {"path": str(getattr(args, name).absolute()), "sha256": getattr(args, name + "_sha256")}
    try:
        if args.action == "station":
            result = export_station(ref("config"), args.output.absolute(), args.interpreter.absolute(),
                                    enabled=args.enable_reviewed_station)
        elif args.action == "approve":
            result = approve(ref("candidate"), ref("review"), args.output.absolute())
        else:
            result = first_cycle(ref("config"), ref("authorization"), deploy.load(ref("request")),
                                 execute=args.action != "check", recover=args.action == "recover")
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0 if result.get("status") in (None, "checked", "review-required", "recovered") else 2
    except (ValueError, OSError, KeyError, TypeError, queue.sqlite3.Error):
        print(json.dumps({"status": "blocked", "hardware_validated": False,
                          "reason": "外部首次核定契約或證據未通過；保留既有隔離"}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
