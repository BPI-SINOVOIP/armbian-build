import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from bpi_lab_network import select_local_ipv4


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.addresses = [{"ifname": "eth0", "flags": ["UP", "LOWER_UP"], "addr_info": [
            {"family": "inet", "scope": "global", "local": "192.168.50.86", "prefixlen": 24}]}]
        self.routes = [{"dst": "192.168.50.188", "dev": "eth0", "prefsrc": "192.168.50.86"}]

    def test_names(self):
        for name in ("eth0", "end0", "enP3p49s0", "wlan0", "wlp1s0"):
            self.addresses[0]["ifname"] = self.routes[0]["dev"] = name
            self.assertEqual(select_local_ipv4(self.addresses, self.routes, "192.168.50.188"),
                             (name, "192.168.50.86"))

    def test_route_rejections(self):
        for change in ({"gateway": "192.168.50.1"}, {"type": "local"}, {"dev": "lo"},
                       {"dst": "192.168.50.100"}, {"prefsrc": "192.168.50.87"},
                       {"dev": "eth0;id"}):
            routes = [dict(self.routes[0], **change)]
            with self.subTest(change=change), self.assertRaises(ValueError):
                select_local_ipv4(self.addresses, routes, "192.168.50.188")

    def test_down_missing_duplicate(self):
        for addresses, routes in (([], self.routes), (self.addresses * 2, self.routes),
                                  (self.addresses, []), (self.addresses, self.routes * 2)):
            with self.assertRaises(ValueError):
                select_local_ipv4(addresses, routes, "192.168.50.188")
        self.addresses[0]["flags"] = ["UP"]
        with self.assertRaises(ValueError):
            select_local_ipv4(self.addresses, self.routes, "192.168.50.188")

    def test_prefix(self):
        for prefix in (32, 33, -1, True, None):
            addresses = copy.deepcopy(self.addresses)
            addresses[0]["addr_info"][0]["prefixlen"] = prefix
            with self.assertRaises(ValueError):
                select_local_ipv4(addresses, self.routes, "192.168.50.188")


if __name__ == "__main__":
    unittest.main()
