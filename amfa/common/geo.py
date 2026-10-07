from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

from amfa import config
from amfa.common.crypto import keyed_hash

log = logging.getLogger("amfa.geo")


@dataclass(frozen=True)
class Place:
    network: str
    country: Optional[str]
    city: Optional[str]
    private: bool

    @property
    def place_key(self) -> Optional[str]:
        if not self.country:
            return None
        return f"{self.country}:{self.city or '-'}"


def parse_ip(raw: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    ip = ipaddress.ip_address(raw.strip())
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip


def network_prefix(ip) -> str:
    if isinstance(ip, ipaddress.IPv4Address):
        return str(ipaddress.ip_network(f"{ip}/24", strict=False))
    return str(ipaddress.ip_network(f"{ip}/48", strict=False))


@lru_cache(maxsize=1)
def _reader():
    path = config.get().geoip_db
    if not path:
        return None
    if not path.exists():
        log.warning("AMFA_GEOIP_DB=%s does not exist; location falls back to network only", path)
        return None
    import maxminddb
    return maxminddb.open_database(str(path))


def geoip_available() -> bool:
    return _reader() is not None


def locate(raw_ip: str) -> Place:
    ip = parse_ip(raw_ip)
    private = bool(ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved)
    network = keyed_hash(network_prefix(ip), "network")
    country = city = None
    reader = _reader()
    if reader is not None and not private:
        try:
            rec = reader.get(str(ip)) or {}
            country = (rec.get("country") or {}).get("iso_code")
            names = (rec.get("city") or {}).get("names") or {}
            city = names.get("en")
            if city:
                city = city[:64]
        except Exception:
            country = city = None
    return Place(network=network, country=country, city=city, private=private)
