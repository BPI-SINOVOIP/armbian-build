#!/usr/bin/env python3
"""以建置快照的真實接收函式驗證外層期限；不模擬電氣或真實 UART。"""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_bpi_sram_driver_model import c_definition


PREAMBLE = r"""
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define xyzModem_MAX_RETRIES 20
#define xyzModem_timeout (-3)
#define xyzModem_cancel (-5)
#define xyzModem_eof (-4)
#define xyzModem_sequence (-8)
#define xyzModem_xmodem 1
#define xyzModem_ymodem 2
#define ACK 6
#define NAK 21
#define IS_ENABLED(x) MODE
#define ZM_DEBUG(x) do {} while (0)
#define CYGACC_COMM_IF_PUTC(a,b) do {} while (0)
static unsigned long now;
static unsigned long transfer_start, transfer_activity;
static unsigned calls, mode;
static char data[128];
static struct {
  bool at_eof, first_xmodem_packet, tx_ack, crc_mode;
  int len, mode, blk, next_blk, total_retries;
  unsigned long timeout, initial_time, file_length, read_length;
  char *bufp;
} xyz;
static unsigned long get_timer(unsigned long base) { return now - base; }
int sup_io_live(void);
void sup_io_progress(void);
static int xyzModem_get_hdr(void) {
  if (++calls > 1000) exit(90);
  now += 1000;
  if (mode == 1 || (mode == 2 && calls == 1)) {
    sup_io_progress();
    xyz.len = sizeof(data);
    xyz.bufp = data;
    xyz.blk = xyz.next_blk;
    return 0;
  }
  return xyzModem_timeout;
}
"""

MAIN = r"""
int main(int argc, char **argv) {
  if (argc != 2) return 2;
  char buf[256];
  int err = 0, size = sizeof(buf);
  xyz.first_xmodem_packet = true;
  xyz.timeout = 30000;
  xyz.mode = xyzModem_xmodem;
  xyz.next_blk = 1;
  if (!strcmp(argv[1], "progress-window") || !strcmp(argv[1], "total-deadline")) {
    now = 110000;
    sup_io_progress();
    now = !strcmp(argv[1], "progress-window") ? 119999 : 120000;
    if (now == 120000) sup_io_progress();
    printf("{\"live\":%d}\n", sup_io_live());
    return 0;
  }
  if (!strcmp(argv[1], "expired")) now = 10000;
  if (!strcmp(argv[1], "valid")) { mode = 1; size = 128; }
  if (!strcmp(argv[1], "partial")) mode = 2;
  if (!strcmp(argv[1], "buffered-expired")) {
    now = 10000;
    xyz.len = 128;
    xyz.bufp = data;
  }
  int count = xyzModem_stream_read(buf, size, &err);
  printf("{\"milliseconds\":%lu,\"calls\":%u,\"count\":%d,\"error\":%d}\n",
         now, calls, count, err);
  return 0;
}
"""


class XmodemDeadlineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = os.environ.get("BPI_SRAM_BUILD")
        if not root:
            raise RuntimeError("必須指定 BPI_SRAM_BUILD，不以缺少快照視為通過")
        source = Path(root) / "source/common/xyzModem.c"
        cls.temp = tempfile.TemporaryDirectory(prefix="bpi-xmodem-test-")
        cls.addClassCleanup(cls.temp.cleanup)
        directory = Path(cls.temp.name)
        unit = directory / "model.c"
        supervisor = Path(root) / "source/arch/arm/mach-sunxi/supervisor.c"
        unit.write_text(PREAMBLE + c_definition(supervisor, "sup_io_live") +
                        c_definition(supervisor, "sup_io_progress") +
                        c_definition(source, "xyzModem_stream_read") + MAIN)
        cls.models = {}
        for enabled in (False, True):
            target = directory / f"model-{int(enabled)}"
            command = ["cc", "-std=c11", "-O2", f"-DMODE={int(enabled)}"]
            if enabled:
                command.append("-DCONFIG_BPI_SRAM_SUPERVISOR=1")
            subprocess.run([*command, str(unit), "-o", str(target)], check=True,
                           capture_output=True, timeout=30)
            cls.models[enabled] = target

    def run_model(self, case, enabled=True):
        result = subprocess.run([str(self.models[enabled]), case], check=True,
                                capture_output=True, text=True, timeout=3)
        return json.loads(result.stdout)

    def test_no_first_packet_honors_total_deadline(self):
        result = self.run_model("empty")
        self.assertEqual(result, {"milliseconds": 10000, "calls": 10,
                                  "count": 0, "error": -3})

    def test_expired_deadline_does_not_retry(self):
        result = self.run_model("expired")
        self.assertEqual(result["calls"], 0)
        self.assertEqual(result["error"], -3)

    def test_expired_deadline_does_not_copy_buffer(self):
        result = self.run_model("buffered-expired")
        self.assertEqual(result["calls"], 0)
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["error"], -3)

    def test_partial_transfer_retains_count_but_reports_timeout(self):
        result = self.run_model("partial")
        self.assertEqual(result["count"], 128)
        self.assertEqual(result["error"], -3)
        self.assertEqual(result["milliseconds"], 11000)

    def test_valid_packet_still_returns(self):
        result = self.run_model("valid")
        self.assertEqual(result["count"], 128)
        self.assertEqual(result["error"], 0)
        self.assertEqual(result["calls"], 1)

    def test_non_supervisor_retains_upstream_initial_timeout(self):
        result = self.run_model("empty", enabled=False)
        self.assertEqual(result["error"], -3)
        self.assertGreater(result["milliseconds"], 30000)

    def test_activity_extends_idle_window_not_total_deadline(self):
        self.assertEqual(self.run_model("progress-window"), {"live": 1})
        self.assertEqual(self.run_model("total-deadline"), {"live": 0})


if __name__ == "__main__":
    unittest.main()
