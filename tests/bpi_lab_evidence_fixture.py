"""完整合成原生證據；只供離線測試，不宣稱任何實板資格。"""

import copy
import hashlib
from pathlib import Path
import uuid

from tools import bpi_lab_backend as backend
import test_bpi_lab_deploy as deploy_data
import test_bpi_lab_linux as linux_data


class EvidenceFixture:
    def observed(self, mode, request, boot_id):
        from test_bpi_lab_session import SessionTests
        identity = SessionTests.identity(self, mode)
        identity["boot_id"] = boot_id
        expected = self.expected if mode == "customer" else self.lifecycle["rescue_expected"]
        identity["machine"] = {"arm64": "aarch64", "arm32": "armv7l", "riscv64": "riscv64"}[expected["architecture"]]
        identity["dt_compatible"] = expected["dt_compatible"]
        wanted = self.bundle["customer_ssh"] if mode == "customer" else self.contract["ssh"]
        host = wanted["host"] if wanted["port"] == 22 else f"[{wanted['host']}]:{wanted['port']}"
        self.evidence_counter = getattr(self, "evidence_counter", 0) + 1
        path = self.root / ("hosts-" + str(self.evidence_counter))
        path.write_bytes((host + " " + identity["host_key"] + "\n").encode())
        return {"schema": "bpi-lab-session-v1", "binding": {key: request[key] for key in backend.session.BINDINGS},
                "mode": mode, "boot_id": boot_id, "identity": identity,
                "ssh": {**wanted, "known_hosts": self.reference(path)},
                "uart_verified": True, "ssh_verified": True, "hostkey_source": "same-session-uart"}

    def media_proof(self, *, final=False):
        proof = {"expected": self.contract["expected"],
                 "source": {key: self.source_record[key] for key in ("raw", "compressed")},
                 "protected_sd": {key: self.contract["protected_sd"][key] for key in ("cid", "controller")},
                 "rescue_schema": self.contract["rescue"]["schema"],
                 "rescue_expected": {key: self.contract["rescue"][key] for key in ("kernel", "identity_sha256")},
                 "backup_manifest_sha256": self.contract["backup"]["sha256"],
                 "confirm_overwrite": final, "backup_verified": True}
        state = deploy_data.state(proof, final)
        state["identity"].update(self.contract["expected"])
        state["sd_before"]["identity"].update(self.contract["protected_sd"])
        state["sd_before"]["prefix"] = self.contract["sd_prefix"]
        return {"schema": "bpi-h618-emmc-deploy-v1" if final else "bpi-lab-deploy-preflight-v1",
                "status": "verified", "request": proof, "remote_state" if final else "state": state,
                **({"ok": True} if final else {})}

    def collection(self, observed):
        identity = observed["identity"]
        collection = linux_data.fixture(self.expected["architecture"])
        hosts = backend.deploy.checked_bytes(observed["ssh"]["known_hosts"])
        collection.update(alias="bpi-lab", known_hosts={"bytes": len(hosts), "sha256": hashlib.sha256(hosts).hexdigest()})
        obs, root = collection["observation"], identity["root"]
        obs["uname"]["value"].update(machine=identity["machine"], release=identity["kernel"])
        obs["dt_compatible"]["value"] = identity["dt_compatible"]
        obs["mounts"]["value"][0].update(major_minor=root["devnum"], fs_type=root["fs"])
        obs["root"]["value"].update(major_minor=root["devnum"], stat_major_minor=root["devnum"],
                                   sysfs_major_minor=root["devnum"], uuid_major_minor=root["devnum"],
                                   sysfs_path=root["sysfs"], uuid_sysfs_path=root["sysfs"],
                                   parent_path=root["parent"], parent_major_minor=root["parent_devnum"],
                                   uuid=root["uuid"], bytes=512)
        obs["media"]["value"] = [
            {"name": item["name"], "sysfs_path": item["sysfs"], "device_path": str(Path(item["sysfs"]).parent.parent),
             "major_minor": item["devnum"], "cid": item["cid"], "type": item["type"], "controller": item["controller"],
             "bytes": item["bytes"], "sectors": item["bytes"] // 512, "slaves": [], "is_partition": False}
            for item in identity["media"]]
        obs["root_after"], obs["media_after"] = copy.deepcopy(obs["root"]), copy.deepcopy(obs["media"])
        return collection

    def fake_result(self, context, current, output, timeout):
        request = context[-1]
        stage = request["stage"]
        mode = current["phase"] if "resume" in request else "customer" if stage in ("boot", "smoke") else "rescue"
        boot_id = current["boot_id"] if current else "11111111-2222-3333-4444-555555555555"
        if stage in ("boot", "recovery"):
            boot_id = str(uuid.UUID(int=uuid.UUID(boot_id).int + 1))
        observed = self.observed(mode, request, boot_id)
        if stage == "deploy" or stage == "preflight" and mode == "rescue":
            return {**self.media_proof(final=stage == "deploy"), "session": observed}
        if stage == "recovery":
            return {"schema": "bpi-lab-lifecycle-result-v1", "action": stage, "status": "verified",
                    "rescue_verified": True, "rescue_proof": self.media_proof(), "session": observed}
        collection = self.collection(observed)
        path = output / "linux" / "collection.json"
        path.parent.mkdir(parents=True)
        path.write_bytes(backend.deploy.encode(collection))
        validation = backend.linux.validate(collection, self.expected)
        common = {"session": observed, "linux_collection": self.reference(path)}
        if stage == "smoke":
            return {**validation, **common}
        return {"schema": "bpi-lab-resume-observation-v1" if stage == "preflight" else "bpi-lab-lifecycle-result-v1",
                "status": "verified", "linux": validation, **common,
                **({"action": "boot", "customer_kernel_verified": True} if stage == "boot" else {})}
