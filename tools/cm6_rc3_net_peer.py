#!/usr/bin/env python3
"""在隔離測試網路提供固定 8 MiB 資料與上傳 SHA；不存取磁碟。"""
import argparse
import hashlib
import ipaddress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAYLOAD = bytes(range(256)) * 32768
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def send_data(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/payload":
            self.send_data(200, PAYLOAD)
        elif self.path == "/sha256":
            self.send_data(200, (DIGEST + "\n").encode())
        else:
            self.send_data(404, b"")

    def do_POST(self):
        try:
            size = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            size = -1
        if self.path != "/upload" or size != len(PAYLOAD):
            self.send_data(400, b"")
            return
        data = self.rfile.read(size)
        if len(data) != size:
            self.send_data(400, b"")
            return
        self.send_data(200, (hashlib.sha256(data).hexdigest() + "\n").encode())


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
    parser.add_argument("--bind", required=True, help="明確指定主機的測試網路 IPv4 位址")
    parser.add_argument("--port", type=int, default=8000, help="監聽埠，預設 8000")
    args = parser.parse_args()
    try:
        addr = ipaddress.IPv4Address(args.bind)
    except ipaddress.AddressValueError:
        parser.error("必須提供有效 IPv4 位址")
    if not 1 <= args.port <= 65535:
        parser.error("埠必須介於 1 與 65535")
    if addr.is_unspecified or addr.is_multicast:
        parser.error("不得監聽全部網卡或群播位址")
    try:
        server = ThreadingHTTPServer((str(addr), args.port), Handler)
    except OSError:
        parser.error("無法監聽，請核對本機位址與埠是否可用")
    print(f"已監聽 {addr}:{args.port}；資料容量={len(PAYLOAD)} SHA256={DIGEST}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
