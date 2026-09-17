"""合成 sysfs 與記憶體 I/O 回歸；不建立或開啟實體設備。"""

from contextlib import contextmanager
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import tempfile
from types import SimpleNamespace
import unittest

from tools import bpi_lab_media as media


class FakeOps(media.NativeOps):
    def __init__(self, root):
        self.root = Path(root)
        self.nodes, self.fds, self.data = {}, {}, {}
        self.opens, self.writes, self.flushes = [], [], []
        self.event = False
        self.ioctl_fault = None
        self.short_write = None
        self.fail_write = False
        self.corrupt_readback = False
        self.leased = False
        self.closed_while_leased = []
        self.on_write = None
        self.rescue_blob = json.dumps({"schema": "bpi-external-rescue-v1", "kernel": "6.1.1-lab"}).encode()

    def stat(self, path, *, follow_symlinks=True):
        if str(path) == "/":
            return SimpleNamespace(st_dev=os.makedev(0, 1))
        if str(path) in self.nodes:
            row = self.nodes[str(path)]
            return SimpleNamespace(st_mode=stat.S_IFBLK | 0o600, st_rdev=row["rdev"], st_dev=0)
        return os.stat(path, follow_symlinks=follow_symlinks)

    def read(self, path, maximum=65536):
        if str(path) == "/etc/bpi-rescue.json":
            return self.rescue_blob
        return super().read(path, maximum)

    def read_regular(self, path, maximum=65536):
        if str(path) == "/etc/bpi-rescue.json":
            return self.rescue_blob
        return super().read_regular(path, maximum)

    def getuid(self):
        return 0

    def uname(self):
        return SimpleNamespace(release="6.1.1-lab")

    def open_device(self, path, writable=False):
        fd = 1000 + len(self.opens)
        self.opens.append((path, writable))
        self.fds[fd] = (path, writable, copy.deepcopy(self.nodes[path]))
        return fd

    def close(self, fd):
        self.closed_while_leased.append(self.leased)
        del self.fds[fd]

    def fstat(self, fd):
        return SimpleNamespace(st_mode=stat.S_IFBLK | 0o600, st_rdev=self.fds[fd][2]["rdev"])

    def ioctl(self, fd, command, buffer=b""):
        row = self.fds[fd][2]
        if command == 0x1261:
            self.flushes.append(fd)
            return 0
        values = {0x80081272: ("=Q", row["bytes"]), 0x1268: ("=I", 512),
                  0x127b: ("=I", 4096), 0x80081280: ("=Q", row["diskseq"])}
        fmt, value = values[command]
        if self.ioctl_fault == command:
            value += 1
        return struct.pack(fmt, value)

    def pread(self, fd, count, offset):
        data = bytes(self.data[self.fds[fd][0]][offset:offset + count])
        if self.corrupt_readback and self.writes and self.fds[fd][1] and offset == 0:
            data = b"!" + data[1:]
        return data

    def pwrite(self, fd, data, offset):
        path, writable, _ = self.fds[fd]
        assert writable
        if self.fail_write:
            raise OSError("合成寫入失敗")
        count = min(len(data), self.short_write) if self.short_write else len(data)
        self.writes.append((path, offset, count))
        self.data[path][offset:offset + count] = data[:count]
        if self.on_write:
            self.on_write()
        return count

    def fsync(self, fd):
        self.flushes.append(fd)

    @contextmanager
    def watch(self):
        def poll():
            media.require(not self.event, "合成拔插事件")
        yield SimpleNamespace(poll=poll)

    @contextmanager
    def lease(self, key):
        assert not self.leased and key.startswith("media:")
        self.leased = True
        try:
            yield
        finally:
            self.leased = False


class FileOps(FakeOps):
    """只對合成的一般檔執行真正 pread／pwrite／fsync；區塊 ioctl 仍明示為替身。"""

    def __init__(self, original):
        super().__init__(original.root)
        self.nodes = copy.deepcopy(original.nodes)
        self.rescue_blob = original.rescue_blob
        for path, data in original.data.items():
            Path(path).write_bytes(data)

    def open_device(self, path, writable=False):
        assert str(path).startswith(str(self.root / "dev") + "/")
        assert stat.S_ISREG(os.stat(path, follow_symlinks=False).st_mode)
        fd = os.open(path, (os.O_RDWR if writable else os.O_RDONLY) | os.O_NOFOLLOW | os.O_CLOEXEC)
        self.opens.append((path, writable))
        self.fds[fd] = (path, writable, copy.deepcopy(self.nodes[path]))
        return fd

    def pread(self, fd, count, offset):
        assert fd in self.fds
        return os.pread(fd, count, offset)

    def pwrite(self, fd, data, offset):
        path, writable, _ = self.fds[fd]
        assert writable
        count = os.pwrite(fd, data, offset)
        self.writes.append((path, offset, count))
        return count

    def fsync(self, fd):
        os.fsync(fd)
        self.flushes.append(fd)

    def close(self, fd):
        os.close(fd)
        super().close(fd)


class Fixture:
    def __init__(self, root, kind="usb"):
        self.root = Path(root)
        self.sys, self.proc, self.dev = (self.root / part for part in ("sys", "proc", "dev"))
        for path in (self.sys / "class/block", self.sys / "dev/block", self.proc / "self", self.dev):
            path.mkdir(parents=True)
        self.ops = FakeOps(root)
        self.put(self.proc / "self/mountinfo", "1 0 0:1 / / rw - rootfs rootfs rw\n")
        self.put(self.proc / "swaps", "Filename\tType\tSize\tUsed\tPriority\n")
        self.target = self.usb() if kind == "usb" else self.nvme()
        self.sd = self.add_sd()
        self.kwargs = {"sysroot": str(self.sys), "procroot": str(self.proc), "devroot": str(self.dev), "ops": self.ops}

    def put(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(value))

    def link(self, path, target):
        path.parent.mkdir(parents=True, exist_ok=True)
        target.mkdir(parents=True, exist_ok=True)
        if not path.is_symlink():
            path.symlink_to(target)

    def bus(self, path, name):
        self.link(path / "subsystem", self.sys / "bus" / name)

    def block(self, name, base, device, number, capacity=128 * 1024):
        self.link(self.sys / "class/block" / name, base)
        self.link(self.sys / "dev/block" / number, base)
        self.link(base / "device", device)
        for directory in ("holders", "slaves", "queue"):
            (base / directory).mkdir(exist_ok=True)
        for key, value in {"size": capacity // 512, "dev": number, "diskseq": 3,
                           "queue/logical_block_size": 512, "queue/physical_block_size": 4096,
                           "queue/zoned": "none"}.items():
            self.put(base / key, value)
        self.put(self.dev / name, "合成節點；不是真實設備")
        major, minor = map(int, number.split(":"))
        self.ops.nodes[str(self.dev / name)] = {"rdev": os.makedev(major, minor), "bytes": capacity, "diskseq": 3}
        self.ops.data[str(self.dev / name)] = bytearray(capacity)
        return base

    def usb(self, name="sda", number="8:0", port="2", wwid="naa.5000000000000001"):
        controller = self.sys / "devices/platform/usb-controller"
        hub = controller / "usb1"
        bridge = hub / ("1-" + port)
        interface = bridge / ("1-" + port + ":1.0")
        device = interface / "host3/target3:0:0/3:0:0:0"
        self.bus(hub, "usb")
        self.bus(bridge, "usb")
        self.bus(interface, "usb")
        self.link(interface / "driver", self.sys / "bus/usb/drivers/uas")
        for path, value in ((hub / "devpath", "0"), (bridge / "devpath", port),
                            (bridge / "serial", "BRIDGE-123"), (bridge / "idVendor", "1234"),
                            (bridge / "idProduct", "abcd"), (interface / "bInterfaceClass", "08"),
                            (device / "type", "0"), (device / "wwid", wwid)):
            self.put(path, value)
        self.block(name, device / "block" / name, device, number)
        return {"kind": "usb", "identity": {"wwid": wwid, "serial": "BRIDGE-123", "vid": "1234", "pid": "abcd", "lun": 0},
                "bytes": 128 * 1024, "logical_block_size": 512, "physical_block_size": 4096,
                "topology": {"controller": "/sys/devices/platform/usb-controller", "port": port}}

    def nvme(self):
        pci = self.sys / "devices/pci0000:00/0000:00:01.0"
        controller = pci / "nvme/nvme2"
        self.bus(pci, "pci")
        self.bus(controller, "nvme")
        self.link(controller / "device", pci)
        for key, value in {"transport": "pcie", "serial": "NVME-123", "model": "TEST-NVME"}.items():
            self.put(controller / key, value)
        base = self.block("nvme2n1", controller / "nvme2n1", controller, "259:0")
        self.put(base / "wwid", "eui.1234567890abcdef")
        self.put(base / "nsid", "1")
        return {"kind": "nvme", "identity": {"wwid": "eui.1234567890abcdef", "serial": "NVME-123", "model": "TEST-NVME", "nsid": 1},
                "bytes": 128 * 1024, "logical_block_size": 512, "physical_block_size": 4096,
                "topology": {"controller": "/sys/devices/pci0000:00/0000:00:01.0"}}

    def add_sd(self):
        controller = self.sys / "devices/platform/mmc-controller"
        device = controller / "mmc_host/mmc0/mmc0:0001"
        self.put(device / "type", "SD")
        self.put(device / "cid", "1" * 32)
        self.block("mmcblk0", device / "block/mmcblk0", device, "179:0", 16384)
        return {"cid": "1" * 32, "bytes": 16384, "controller": "/sys/devices/platform/mmc-controller",
                "full_sha256": hashlib.sha256(bytes(16384)).hexdigest()}

    def partition(self, parent="sda", name="sda1", number="8:1"):
        base = (self.sys / "class/block" / parent).resolve() / name
        self.link(self.sys / "class/block" / name, base)
        self.link(self.sys / "dev/block" / number, base)
        for key, value in {"partition": "1", "start": "8", "size": "32", "dev": number}.items():
            self.put(base / key, value)
        (base / "holders").mkdir()
        self.put(self.dev / name, "合成分割節點")
        major, minor = map(int, number.split(":"))
        self.ops.nodes[str(self.dev / name)] = {"rdev": os.makedev(major, minor), "bytes": 16384, "diskseq": 3}
        return base


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = Fixture(self.temp.name)

    def test_usb_and_fd(self):
        f = self.fixture
        result = media.inspect(f.target, **f.kwargs)
        self.assertEqual(result["identity"], f.target["identity"])
        self.assertNotIn("cid", result)
        fd = f.ops.open_device(result["device"])
        media.check_fd(fd, result, ops=f.ops)
        f.ops.close(fd)

    def test_nvme(self):
        with tempfile.TemporaryDirectory() as path:
            f = Fixture(path, "nvme")
            self.assertEqual(media.inspect(f.target, **f.kwargs)["identity"]["nsid"], 1)

    def test_invalid_wwid_never_falls_back_to_serial_or_cid(self):
        for value in ("", "CID=" + "1" * 32, "naa.bad", "naa.0000000000000000", "t10.        "):
            with self.subTest(value=value):
                target = copy.deepcopy(self.fixture.target)
                target["identity"]["wwid"] = value
                with self.assertRaises(ValueError):
                    media.validate_expected(target)
        self.assertEqual(self.fixture.ops.opens, [])

    def test_topology_and_duplicate_wwid(self):
        f = self.fixture
        wrong = copy.deepcopy(f.target)
        wrong["topology"]["port"] = "3"
        self.assertEqual(media.media_identity(wrong), media.media_identity(f.target))
        with self.assertRaisesRegex(ValueError, "拓撲"):
            media.inspect(wrong, **f.kwargs)
        f.usb("sdb", "8:16", "3")
        with self.assertRaisesRegex(ValueError, "唯一"):
            media.inspect(f.target, **f.kwargs)

    def test_readonly_observation_allows_mounted_customer(self):
        f = self.fixture
        f.partition()
        f.put(f.proc / "self/mountinfo", "1 0 8:1 / / rw - ext4 /dev/sda1 rw\n")
        with self.assertRaisesRegex(ValueError, "掛載"):
            media.inspect(f.target, **f.kwargs)
        result = media.inspect(f.target, **f.kwargs, require_idle=False)
        self.assertTrue(result["mounted"])
        self.assertEqual(result["partitions"][0]["index"], 1)

    def test_mount_in_other_namespace(self):
        f = self.fixture
        f.put(f.proc / "7/ns/mnt", "合成 namespace")
        f.put(f.proc / "7/mountinfo", "1 0 8:0 / /mnt rw - ext4 /dev/sda rw\n")
        with self.assertRaisesRegex(ValueError, "掛載"):
            media.inspect(f.target, **f.kwargs)

    def test_swap_and_holders(self):
        f = self.fixture
        part = f.partition()
        f.put(f.proc / "swaps", f"Filename Type Size Used Priority\n{f.dev}/sda1 partition 16 0 -2\n")
        with self.assertRaises(ValueError):
            media.inspect(f.target, **f.kwargs)
        f.put(f.proc / "swaps", "Filename Type Size Used Priority\n")
        (part / "holders/dm-0").mkdir()
        with self.assertRaises(ValueError):
            media.inspect(f.target, **f.kwargs)

    def test_fd_all_ioctl_mismatches(self):
        f = self.fixture
        observed = media.inspect(f.target, **f.kwargs)
        fd = f.ops.open_device(observed["device"])
        for command in (0x80081272, 0x1268, 0x127b, 0x80081280):
            f.ops.ioctl_fault = command
            with self.subTest(command=command), self.assertRaises(ValueError):
                media.check_fd(fd, observed, ops=f.ops)

    def test_inventory_includes_sd_and_partitions(self):
        f = self.fixture
        f.partition()
        self.assertEqual({row["name"] for row in media.inventory(**f.kwargs)}, {"sda", "sda1", "mmcblk0"})
        self.assertEqual(media.inspect_sd(f.sd, **f.kwargs)["cid"], f.sd["cid"])

    def test_missing_diskseq_is_explicit(self):
        f = self.fixture
        (f.sys / "class/block/sda/diskseq").unlink()
        self.assertIsNone(media.inspect(f.target, **f.kwargs)["diskseq"])

    def test_invalid_nvme_namespace_identifier(self):
        with tempfile.TemporaryDirectory() as path:
            f = Fixture(path, "nvme")
            for wwid in ("eui.0", "eui.0000000000000000", "uuid.00000000-0000-0000-0000-000000000000",
                         "nvme.1234-31-32-00000001"):
                target = copy.deepcopy(f.target)
                target["identity"]["wwid"] = wwid
                with self.subTest(wwid=wwid), self.assertRaises(ValueError):
                    media.validate_expected(target)

    def test_native_open_rejects_ordinary_file_without_device_io(self):
        path = self.fixture.root / "ordinary"
        path.write_bytes(b"fixture")
        with self.assertRaisesRegex(ValueError, "非區塊"):
            media.NativeOps().open_device(path, writable=True)
        link = self.fixture.root / "link"
        link.symlink_to(path)
        with self.assertRaises(ValueError):
            media.NativeOps().read_regular(link)

    def test_native_event_overflow_and_block_events_are_rejected(self):
        from unittest import mock
        watch = media.DeviceWatch.__new__(media.DeviceWatch)
        watch.socket = mock.Mock()
        watch.socket.recvmsg.return_value = (b"change@/devices/x\0SUBSYSTEM=block\0", [], 0, (0, 1))
        with self.assertRaisesRegex(ValueError, "事件"):
            watch.poll()
        watch.socket.recvmsg.return_value = (b"change@/devices/x\0", [], 32, (0, 1))
        with self.assertRaisesRegex(ValueError, "完整"):
            watch.poll()
        watch.socket.recvmsg.side_effect = OSError("合成 ENOBUFS")
        with self.assertRaises(OSError):
            watch.poll()


if __name__ == "__main__":
    unittest.main()
