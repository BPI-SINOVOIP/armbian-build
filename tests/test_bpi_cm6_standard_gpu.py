"""標準 CM6 Noble XFCE 配置修正的限定回歸；不建 DEB、不連板。"""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("cm6_standard_gpu", REPO / "tools/bpi_cm6_standard_gpu.py")
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)
ORIGINAL = ('Section "Device"\n        Identifier "nogpu"\n        Driver "modesetting"\n'
            '        Option "Accelmethod" "none"\nEndSection\n\nSection "Module"\n'
            '        Disable "glamoregl"\nEndSection\n')


class StandardGPUConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "rootfs"
        self.root.mkdir()
        self.file(MOD.XORG_POLICY, ORIGINAL)
        self.file(MOD.LIGHTDM_DEFAULT, "[Seat:*]\nxserver-command=X -core\n")
        self.file("etc/lightdm/lightdm.conf.d/11-armbian.conf", "[Seat:*]\nuser-session=xfce\nallow-guest=false\n")
        self.file("etc/armbian-release", "BOARD=bananapicm6\nKERNEL_TARGET=legacy\n")
        self.file("etc/os-release", "ID=ubuntu\nVERSION_CODENAME=noble\n")
        self.file("usr/share/xsessions/xfce.desktop", "[Desktop Entry]\nName=Xfce\n")
        self.packages = [{"package":p,"version":"1","architecture":"riscv64"} for p in
                         ("libc6","lightdm","xfce4-session","xfwm4","linux-image-legacy-spacemit")]
        self.file("var/lib/dpkg/status", "\n\n".join(
            "Package: {package}\nVersion: {version}\nArchitecture: {architecture}\nStatus: install ok installed".format(**r)
            for r in self.packages) + "\n")
        for name in ("vmlinuz-", "config-"):
            self.file("boot/" + name + "6.6.36-legacy-spacemit", "fixture\n")

    def file(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def test_exact_configuration_pair_and_provenance(self):
        self.assertEqual(hashlib.sha256(ORIGINAL.encode()).hexdigest(), MOD.XORG_BEFORE_SHA)
        changes, writes = MOD.plan_xfce_adaptations(self.root)
        self.assertEqual([x["before_sha256"] for x in changes], [MOD.XORG_BEFORE_SHA, None])
        self.assertEqual(hashlib.sha256(writes[0][1]).hexdigest(), MOD.XORG_AFTER_SHA)
        self.assertEqual(writes[1][1].decode(), MOD.LIGHTDM_CONTENT)
        self.assertEqual(len(changes[1]["inputs"]), 2)
        self.assertEqual((self.root / MOD.XORG_POLICY).read_text(), ORIGINAL)
        self.assertFalse((self.root / MOD.LIGHTDM_PVR).exists())

    def test_reject_changed_official_payload(self):
        self.file(MOD.XORG_POLICY, ORIGINAL + "\n")
        with self.assertRaisesRegex(ValueError, "原載荷 SHA"):
            MOD.plan_xfce_adaptations(self.root)

    def test_reject_any_additional_xserver_override(self):
        for path, content in (
            ("etc/lightdm/lightdm.conf.d/10-custom.conf", "[Seat:*]\nxserver-command=X -core\n"),
            ("etc/lightdm/lightdm.conf.d/99-custom.conf", "[Seat:*]\nxserver-command=X -nocursor\n"),
            ("etc/lightdm/lightdm.conf", "[Seat:seat0]\nxserver-command=X -core\n"),
            ("etc/xdg/lightdm/lightdm.conf.d/99-custom.conf", "[Seat:*]\nxserver-command=X\n"),
        ):
            with self.subTest(path=path):
                f = self.file(path, content)
                with self.assertRaisesRegex(ValueError, "額外覆寫"):
                    MOD.plan_xfce_adaptations(self.root)
                f.unlink()
        self.assertEqual((self.root / MOD.XORG_POLICY).read_text(), ORIGINAL)

    def test_reject_unknown_default_command_or_session(self):
        self.file(MOD.LIGHTDM_DEFAULT, "[Seat:*]\nxserver-command=X\n")
        with self.assertRaisesRegex(ValueError, "X -core"):
            MOD.plan_xfce_adaptations(self.root)
        self.file(MOD.LIGHTDM_DEFAULT, "[Seat:*]\nxserver-command=X -core\n")
        self.file("etc/lightdm/lightdm.conf.d/11-armbian.conf", "[Seat:*]\nuser-session=gnome\n")
        with self.assertRaisesRegex(ValueError, "XFCE"):
            MOD.plan_xfce_adaptations(self.root)

    def test_reject_existing_target_and_linked_configuration(self):
        path = self.file(MOD.LIGHTDM_PVR, MOD.LIGHTDM_CONTENT)
        with self.assertRaisesRegex(ValueError, "已存在"):
            MOD.plan_xfce_adaptations(self.root)
        path.unlink()
        path = self.root / "etc/lightdm/lightdm.conf.d/99-linked.conf"
        path.symlink_to(self.root / MOD.LIGHTDM_DEFAULT)
        with self.assertRaisesRegex(ValueError, "符號連結"):
            MOD.plan_xfce_adaptations(self.root)

    def test_scope_rejects_vendor_release_branch_and_gnome(self):
        _, selected = MOD.scoped_lock()
        with mock.patch.object(MOD.acceleration, "preflight", return_value={"passed":True,"errors":[]}):
            MOD.check_root(self.root, selected)
            for relative, content in (
                ("etc/armbian-release", "BOARD=bananapicm6-titan-emmc\nKERNEL_TARGET=legacy\n"),
                ("etc/armbian-release", "BOARD=bananapicm6\nKERNEL_TARGET=current\n"),
                ("etc/os-release", "ID=ubuntu\nVERSION_CODENAME=jammy\n"),
            ):
                with self.subTest(content=content):
                    before=(self.root/relative).read_text();self.file(relative,content)
                    with self.assertRaises(ValueError):MOD.check_root(self.root,selected)
                    self.file(relative,before)
        with self.assertRaisesRegex(ValueError, "LightDM／XFCE"):
            MOD.check_xfce_scope(self.root, self.packages + [{"package":"gnome-shell"}])

    def test_finish_keeps_official_payload_and_records_adapted_files(self):
        work = self.base / "gpu-work";work.mkdir()
        target = self.root / ("var/tmp/bpi-cm6-standard-gpu-" + "a"*32)
        target.mkdir(parents=True)
        self.file("etc/environment", "LANG=C.UTF-8\n")
        lock, _ = MOD.scoped_lock()
        records = [{"filename":Path(lock["packages"][key]["Filename"]).name} for key in MOD.GPU_IDS]
        packages = [{"package":lock["packages"][key]["Package"],"version":lock["packages"][key]["Version"],
                     "architecture":lock["packages"][key]["Architecture"]} for key in MOD.GPU_IDS]
        payload = {"path":"/"+MOD.XORG_POLICY,"kind":"configuration","bytes":len(ORIGINAL.encode()),"sha256":MOD.XORG_BEFORE_SHA}
        state = {"schema_version":1,"status":"staged","board":"bananapicm6","root_binding":MOD.native.binding(self.root),
                 "producer_sha256":MOD.common.sha256(MOD.__file__),"lock_sha256":MOD.common.sha256(MOD.LOCK),
                 "stage_path":"/"+target.relative_to(self.root).as_posix(),"kernel":{"fixture":True},
                 "packages":records,"payloads":[payload],"sdl_wayland_lines_before":0,"verified_sources":{"fixture":True}}
        (work/"stage.json").write_text(json.dumps(state));(work/"acceleration.lock.json").write_bytes(MOD.LOCK.read_bytes())
        (target/"transaction.json").write_text(json.dumps({"stage_sha256":MOD.common.sha256(work/"stage.json")}))
        for row in records:(target/row["filename"]).write_bytes(b"fixture")
        mapping={row["filename"]:row for row in records}
        with mock.patch.object(MOD,"check_root",return_value=(packages,{"pending_dependencies":[]})), \
             mock.patch.object(MOD.native,"kernel_evidence",return_value=state["kernel"]), \
             mock.patch.object(MOD,"package_record",side_effect=lambda p,i:mapping[p.name]), \
             mock.patch.object(MOD,"payload_records",side_effect=lambda p,n:[payload] if n=="img-gpu-powervr" else []):
            result=MOD.finish(self.root,work)
        self.assertEqual(result["payloads"],[payload])
        self.assertEqual(result["source_adaptations"][0]["before_sha256"],payload["sha256"])
        self.assertEqual(MOD.common.sha256(self.root/MOD.XORG_POLICY),MOD.XORG_AFTER_SHA)
        self.assertEqual((self.root/MOD.LIGHTDM_PVR).read_text(),MOD.LIGHTDM_CONTENT)
        self.assertEqual((self.root/"etc/environment").read_text(), "LANG=C.UTF-8\nMESA_LOADER_DRIVER_OVERRIDE=pvr\n")
        self.assertEqual(result["hardware_validation"],"pending")
        self.assertEqual(json.loads((self.root/MOD.MANIFEST).read_text()),result)
        self.assertIn("GLX", "".join(result["limitations"]))


if __name__ == "__main__":
    unittest.main()
