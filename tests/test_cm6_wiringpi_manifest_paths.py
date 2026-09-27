"""核對 WiringPi 追溯紀錄不保存動態工作路徑；不編譯或接觸硬體。"""
import importlib.util
import json
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location(
    "wiringpi_manifest_paths", Path(__file__).resolve().parents[1] / "tools/build_bpi_cm6_wiringpi.py")
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


class PortableManifestTests(unittest.TestCase):
    def fixture(self, parent="/private/build-a"):
        recipe = parent + "/recipe"
        cache = parent + "/cache"
        work = cache + "/cm6-wiringpi-random"
        source = work + "/fixed-source"
        output = parent + "/output"
        tool = parent + "/tool/bin/riscv64-linux-gnu-gcc"
        roots = {recipe: "${RECIPE_ROOT}", cache: "${CACHE_ROOT}", work: "${BUILD_ROOT}",
                 source: "${SOURCE_ROOT}", output: "${OUTPUT_ROOT}", tool: "${TOOL:riscv64-linux-gnu-gcc}"}
        record = {"commands": [
            {"argv": [tool, "--version"], "cwd": None, "exit": 0},
            {"argv": ["make", "EXTRA_CFLAGS=-ffile-prefix-map=" + work + "=/usr/src/bpi-cm6-wiringpi",
                      "INCLUDE=-I" + source + "/wiringPi -I" + work + "/dependencies/usr/include",
                      "LIBS=-L" + source + "/wiringPi -Wl,-rpath-link," + work + "/dependencies/usr/lib",
                      "-i", recipe + "/config/patch.patch"], "cwd": source + "/gpio", "exit": 0},
            {"argv": ["dpkg-deb", "--build", work + "/package", output + "/fixed.deb"], "cwd": None, "exit": 0}],
            "toolchain": {"gcc": {"path": tool, "sha256": "a" * 64, "version": "GCC 13.3.0"}},
            "elfs": [{"path": "/usr/bin/gpio", "header": "File: " + work + "/package/usr/bin/gpio\nELF64"}],
            "archive": cache + "/fixed.tar.gz", "sha256": "b" * 64,
            "builder_sha256": "c" * 64, "lock_sha256": "d" * 64,
            "source": {"repository": "https://example.invalid/fixed.git", "commit": "e" * 40,
                       "archive_sha256": "f" * 64, "patches": [{"path": "fixed.patch", "sha256": "0" * 64}]}}
        return roots, record

    def test_embedded_flags_and_cwd(self):
        roots, original = self.fixture()
        result = BUILDER.portable_record(original, roots)
        command = result["commands"][1]
        self.assertEqual(command["cwd"], "${SOURCE_ROOT}/gpio")
        self.assertEqual(command["argv"][1], "EXTRA_CFLAGS=-ffile-prefix-map=${BUILD_ROOT}=/usr/src/bpi-cm6-wiringpi")
        self.assertEqual(command["argv"][2], "INCLUDE=-I${SOURCE_ROOT}/wiringPi -I${BUILD_ROOT}/dependencies/usr/include")
        self.assertEqual(command["argv"][3], "LIBS=-L${SOURCE_ROOT}/wiringPi -Wl,-rpath-link,${BUILD_ROOT}/dependencies/usr/lib")
        self.assertNotIn("/private/build-a", json.dumps(result))

    def test_other_manifest_fields_and_contract_unchanged(self):
        roots, original = self.fixture()
        result = BUILDER.portable_record(original, roots)
        self.assertEqual(result["toolchain"]["gcc"]["path"], "${TOOL:riscv64-linux-gnu-gcc}")
        self.assertEqual(result["elfs"][0]["header"], "File: ${BUILD_ROOT}/package/usr/bin/gpio\nELF64")
        self.assertEqual(result["elfs"][0]["path"], "/usr/bin/gpio")
        self.assertIsNone(result["commands"][0]["cwd"])
        for key in ("source", "sha256", "builder_sha256", "lock_sha256"):
            self.assertEqual(result[key], original[key])
        self.assertIn("/private/build-a", json.dumps(original))

    def test_distinct_build_directories_have_equal_records(self):
        first_roots, first = self.fixture("/private/build-a")
        second_roots, second = self.fixture("/another machine/build b")
        self.assertEqual(BUILDER.portable_record(first, first_roots),
                         BUILDER.portable_record(second, second_roots))

    def test_path_boundary_and_idempotence(self):
        roots, original = self.fixture()
        value = "/private/build-a/cache-other/file"
        self.assertEqual(BUILDER.portable_record(value, roots), value)
        result = BUILDER.portable_record(original, roots)
        self.assertEqual(BUILDER.portable_record(result, roots), result)


if __name__ == "__main__":
    unittest.main()
