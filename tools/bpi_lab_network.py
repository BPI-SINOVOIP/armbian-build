"""只解析已取得的網路資料，不假定介面名稱、不操作設備。"""

import ipaddress
import re


def select_local_ipv4(addresses, routes, peer):
    """核對到指定主機的唯一 IPv4 路由及該介面實際位址。"""
    target = ipaddress.IPv4Address(peer)
    if not isinstance(addresses, list) or not isinstance(routes, list) or len(routes) != 1:
        raise ValueError("網路資料或路由不唯一")
    route = routes[0]
    if not isinstance(route, dict) or route.get("type", "unicast") != "unicast" or route.get("gateway"):
        raise ValueError("測試主機不在可核對的直接路由")
    name, source = route.get("dev", ""), route.get("prefsrc", "")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,15}", name) or name == "lo":
        raise ValueError("介面名稱無效")
    if route.get("dst") != str(target):
        raise ValueError("路由目的不是指定測試主機")
    local = ipaddress.IPv4Address(source)
    if local.is_loopback or local.is_unspecified or local.is_multicast:
        raise ValueError("來源不是可用的單播位址")
    devices = [item for item in addresses if isinstance(item, dict) and item.get("ifname") == name]
    if len(devices) != 1 or not {"UP", "LOWER_UP"} <= set(devices[0].get("flags", [])):
        raise ValueError("路由介面未就緒或不唯一")
    matches = [item for item in devices[0].get("addr_info", [])
               if item.get("family") == "inet" and item.get("scope") == "global"
               and item.get("local") == source]
    if len(matches) != 1:
        raise ValueError("路由來源與介面位址不一致")
    prefix = matches[0].get("prefixlen")
    if type(prefix) is not int or not 1 <= prefix <= 32:
        raise ValueError("網路前綴無效")
    if target not in ipaddress.IPv4Network(f"{source}/{prefix}", strict=False):
        raise ValueError("主機與板子不在同一直接網段")
    return name, source
