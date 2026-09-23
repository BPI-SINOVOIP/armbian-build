#!/usr/bin/env python3
"""僅接收 UART 原始資料；不送字元、不登入、不自動選取串口。"""
import argparse
import datetime
import os
from pathlib import Path


class ChineseParser(argparse.ArgumentParser):
    def format_usage(self):
        return super().format_usage().replace("usage: ", "用法：", 1)

    def format_help(self):
        return super().format_help().replace("usage: ", "用法：", 1)

    def error(self, message):
        self.print_usage()
        self.exit(2, "參數錯誤：請依 --help 核對必要參數及格式。\n")


def main():
    parser = ChineseParser(description=__doc__, add_help=False)
    parser._optionals.title = "選項"
    parser.add_argument("-h", "--help", action="help", help="顯示說明後結束")
    parser.add_argument("--port", required=True, help="人工核對的 UART 裝置路徑")
    parser.add_argument("--output", required=True, type=Path, help="不存在的原始紀錄檔")
    args = parser.parse_args()
    import serial
    os.umask(0o077)
    # 在開啟裝置前指定控制線；不得把轉接器的控制線接到板端重置腳。
    uart = serial.Serial(port=None, baudrate=115200, bytesize=8,
                         parity="N", stopbits=1, timeout=0.5,
                         xonxoff=False, rtscts=False, dsrdtr=False,
                         exclusive=True)
    uart.dtr = False
    uart.rts = False
    with args.output.open("xb") as log:
        uart.port = args.port
        try:
            uart.open()
            print("UART 接收已就緒；現在才上電。按 Ctrl+C 結束。", flush=True)
            print(datetime.datetime.now(datetime.timezone.utc).isoformat(), flush=True)
            while True:
                data = uart.read(4096)
                if data:
                    log.write(data)
                    log.flush()
        except KeyboardInterrupt:
            pass
        finally:
            uart.close()
            os.fsync(log.fileno())
    print("UART 原始資料已保存；請在交付前檢查敏感資訊。")


if __name__ == "__main__":
    main()
