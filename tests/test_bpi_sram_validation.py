#!/usr/bin/env python3
"""驗證器的階段選擇守門；所有子程序均被替代，不連線或操作媒體。"""

from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import validate_bpi_sram as validate


class ValidationGuards(unittest.TestCase):
    def rejected(self, metadata, use_ddr):
        with tempfile.TemporaryDirectory(prefix="bpi-validation-test-") as directory:
            root = Path(directory)
            build = root / "build"
            build.mkdir()
            (build / "build-report.json").write_text(json.dumps(metadata))
            target = root / "report"
            args = ["validate_bpi_sram.py", "--build-dir", str(build), "--output-dir", str(target)]
            if use_ddr:
                args.extend(["--ddr-build-dir", str(root / "ddr")])
            with mock.patch.object(sys, "argv", args), redirect_stderr(io.StringIO()), \
                    mock.patch.object(validate.subprocess, "run") as runner:
                self.assertEqual(validate.main(), 1)
                runner.assert_not_called()
            self.assertFalse(target.exists())

    def test_v2_cannot_run_without_payload_build(self):
        self.rejected({"ddr_v2": True}, False)

    def test_v1_cannot_use_ddr_execution_suite(self):
        self.rejected({"ddr_v2": False}, True)

    def test_old_report_defaults_to_v1(self):
        self.rejected({}, True)

    def test_boolean_field_is_strict(self):
        for value in (0, 1, "true", "false", None, [], {}):
            with self.subTest(value=value):
                self.rejected({"ddr_v2": value}, bool(value))

    def test_report_must_be_object(self):
        for value in ([], None, 0, "report"):
            with self.subTest(value=value):
                self.rejected(value, False)


if __name__ == "__main__":
    unittest.main()
