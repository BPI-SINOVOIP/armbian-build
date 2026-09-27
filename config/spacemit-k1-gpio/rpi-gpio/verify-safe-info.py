#!/usr/bin/env python3
"""只核對真實 CM6 板型與已安裝 Python 模組，不執行腳位操作。"""

import json
from pathlib import Path

model = Path("/proc/device-tree/model").read_bytes().rstrip(b"\0").decode()
compatible = Path("/proc/device-tree/compatible").read_bytes().rstrip(b"\0").decode().split("\0")
assert model == "BananaPi BPI-CM6", "裝置樹型號不符"
assert "bananapi,bpi-cm6" in compatible and "spacemit,k1-x" in compatible, "裝置樹相容性不符"

import RPi.GPIO as GPIO

assert GPIO.VERSION == "0.7.1", "Python API 版本不符"
assert GPIO.RPI_INFO["TYPE"] == "Banana Pi CM6[SpacemiT K1]", "Python 板型識別不符"
assert GPIO.RPI_INFO["PROCESSOR"] == "SpacemiT K1", "Python 處理器識別不符"
assert Path(GPIO.__file__).resolve() == Path("/usr/lib/python3/dist-packages/RPi/GPIO/__init__.py"), "模組不是系統套件路徑"
print(json.dumps({"model": model, "compatible": compatible, "version": GPIO.VERSION,
                  "type": GPIO.RPI_INFO["TYPE"], "processor": GPIO.RPI_INFO["PROCESSOR"],
                  "module": GPIO.__file__, "pin_operations": 0}, ensure_ascii=False))
