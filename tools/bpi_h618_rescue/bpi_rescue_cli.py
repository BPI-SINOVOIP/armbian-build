"""沿用 tools/bpi_sram_package.py 的中文 parser，讓救援包不依賴整個倉庫。"""

import argparse
import sys


class ChineseArgumentParser(argparse.ArgumentParser):
    """讓說明與參數錯誤維持繁體中文，不回傳 argparse 的外語敘述。"""

    def __init__(self, *args, **kwargs):
        kwargs["add_help"] = False
        kwargs["allow_abbrev"] = False
        super().__init__(*args, **kwargs)
        self._positionals.title = "位置參數"
        self._optionals.title = "選項"
        self.add_argument("-h", "--help", action="help", help="顯示說明並離開")

    def format_usage(self):
        return super().format_usage().replace("usage: ", "用法：")

    def format_help(self):
        return super().format_help().replace("usage: ", "用法：")

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, "參數錯誤：參數缺漏、格式無效或不受支援；請以 --help 查看說明。\n")
