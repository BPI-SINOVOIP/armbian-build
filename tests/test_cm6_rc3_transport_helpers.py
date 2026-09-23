"""以回環 HTTP 與 UART 替身驗證資料完整性；不存取板端硬體。"""
import contextlib
import hashlib
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tools' / f'{name}.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


net = load('cm6_rc3_net_peer')
uart = load('cm6_rc3_uart_capture')


class QuietHandler(net.Handler):
    def log_message(self, *args):
        pass


class TransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = net.ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_address[1]}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(3)

    def test_exact_download_and_upload_hash(self):
        with urlopen(self.url + '/payload', timeout=5) as response:
            data = response.read()
        self.assertEqual(len(data), 8388608)
        self.assertEqual(hashlib.sha256(data).hexdigest(),
                         '7d212b9c884f5c77896de960ae17cc341cda43b14d6a971f34ca29ebd4badf7f')
        with urlopen(self.url + '/sha256', timeout=5) as response:
            expected = response.read()
        with urlopen(Request(self.url + '/upload', data=data), timeout=5) as response:
            self.assertEqual(response.read(), expected)

    def test_unknown_path_and_wrong_length_rejected(self):
        for request, status in [(self.url + '/../../etc/passwd', 404),
                                (Request(self.url + '/upload', data='短檔'.encode()), 400)]:
            with self.assertRaises(HTTPError) as caught:
                urlopen(request, timeout=5)
            self.assertEqual(caught.exception.code, status)

    def test_listen_all_is_rejected(self):
        for args in [['--bind', '0.0.0.0'], ['--bind', '224.0.0.1'],
                     ['--bind', '127.0.0.1', '--port', '0'],
                     ['--bind', '127.0.0.1', '--port', '65536'],
                     ['--bind', '無效位址']]:
            with patch.object(sys, 'argv', ['net'] + args), \
                    contextlib.redirect_stderr(io.StringIO()), \
                    contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
                net.main()

    def test_uart_binary_preserved_no_transmit_existing_file_untouched(self):
        samples = [b'\x00CM6\r\n\xff', KeyboardInterrupt()]
        instances = []

        class FakeSerial:
            def __init__(self, **kwargs):
                self.options = kwargs
                self.opened = self.closed = False
                instances.append(self)
            def open(self):
                self.opened = True
            def close(self):
                self.closed = True
            def read(self, size):
                item = samples.pop(0)
                if isinstance(item, BaseException):
                    raise item
                return item
            def write(self, data):
                raise AssertionError('接收器不可送出字元')

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'uart.bin'
            with patch.dict(sys.modules, {'serial': types.SimpleNamespace(Serial=FakeSerial)}), \
                    patch.object(sys, 'argv', ['uart', '--port', '/dev/測試替身', '--output', str(path)]), \
                    contextlib.redirect_stdout(io.StringIO()):
                uart.main()
                self.assertEqual(path.read_bytes(), b'\x00CM6\r\n\xff')
                self.assertTrue(instances[0].closed)
                self.assertFalse(instances[0].dtr)
                self.assertFalse(instances[0].rts)
                self.assertTrue(instances[0].options['exclusive'])
                with self.assertRaises(FileExistsError):
                    uart.main()
                self.assertFalse(instances[1].opened)
                self.assertEqual(path.read_bytes(), b'\x00CM6\r\n\xff')


if __name__ == '__main__':
    unittest.main()
