import os
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="ipdr-test-"))
os.environ["IPDR_STORE_PATH"] = str(_tmp / "test.sqlite3")
os.environ.setdefault("IPDR_API_KEY", "")
os.environ["IPDR_PROVIDER"] = "anthropic"
os.environ["IPDR_MODEL"] = "claude-opus-5-5"
os.environ["IPDR_BASE_URL"] = ""

from ipdr import main, store

store.init()
main.LOG_DIR = _tmp

from ipdr import net


def _no_network(method, url, **kw):
    raise RuntimeError(f"Test tried a real network call: {method} {url}")


net._session.request = _no_network

from ipdr import dictionary

_TYPES = {
    "recv_time": "UInt32", "log_id": "String", "start_time": "UInt32", "end_time": "UInt32", "duration": "UInt64",
    "cap_ip": "LowCardinality(String)", "requested_server": "String", "client_ip": "String", "client_port": "UInt16",
    "client_geo": "LowCardinality(String)", "client_location": "LowCardinality(String)",
    "client_asn": "LowCardinality(String)", "subscriber_id": "String", "imei": "String", "imsi": "String",
    "server_ip": "String", "server_port": "UInt16", "server_geo": "LowCardinality(String)",
    "server_location": "LowCardinality(String)", "server_asn": "LowCardinality(String)",
    "app_category": "LowCardinality(String)", "app_label": "LowCardinality(String)",
    "l4_protocol": "LowCardinality(String)", "c2s_pkt_num": "UInt32", "s2c_pkt_num": "UInt32",
    "c2s_byte_num": "UInt64", "s2c_byte_num": "UInt64", "usr_loc_info": "String", "mcc": "LowCardinality(String)",
    "mnc": "LowCardinality(String)", "lac": "String", "cid": "String", "geo": "String", "loc": "String",
    "block_type": "UInt32",
}
_b = dictionary.base()
_names = list(dict.fromkeys(list((_b.get("columns") or {}).keys()) + list((_b.get("empty_columns") or {}).get("list", []))))
dictionary._live.update(at=10**12, value=[{"name": n, "type": _TYPES.get(n, "String")} for n in _names])

from ipdr import agent

agent.ANSWER_CACHE = False

agent.DEBUG_LOG = _tmp / "agent_debug.jsonl"

from ipdr import catalog

catalog.set_data({
    "client_location": [[v, 1] for v in ["Islamabad Pakistan", "Lahore Pakistan", "Karachi Pakistan", "Pakistan",
                                         "Faisalabad Pakistan", "Punjab Pakistan", "Rawalpindi Pakistan",
                                         "Multan Pakistan", "Peshawar Pakistan", "Hyderabad Pakistan",
                                         "Gujranwala Pakistan", "Quetta Pakistan", "Sialkot Pakistan", "Wah Cantt Pakistan",
                                         "Dera Ghazi Khan Pakistan", "S\u0101diq\u0101b\u0101d Pakistan",
                                         "Kh\u016bsh\u0101b Pakistan", "Kabul Afghanistan", "United States"]],
    "client_asn": [[v, 1] for v in ["Pakistan Telecommunication Company Limited", "CMPak Limited", "PMCL LDI IP TRANSIT",
                                    "Telenor Pakistan", "Nayatel Pvt Ltd", "Wancom Pvt Ltd.", "Fiber Beam Pvt Limited",
                                    "Multan Broadband", "OPENED Networks"]],
    "server_asn": [[v, 1] for v in ["Google LLC", "Amazon.com, Inc.", "Facebook, Inc.", "Servers.com, Inc.",
                                    "Total Uptime Technologies, LLC", "Connect Communications",
                                    "Mobile Telecommunication Company Saudi Arabia Joint-Stock company"]],
    "server_location": [[v, 1] for v in ["United States", "Russia", "Singapore", "Singapore Singapore", "Ireland",
                                         "Dublin Ireland", "Pakistan", "Mumbai India", "Ashburn United States",
                                         "Saudi Arabia"]],
    "app_category": [[v, 1] for v in ["Streaming", "Game", "IM", "Web_Browsing", "Tunneling"]],
    "area": [[v, 1] for v in ["Model Town", "Block A", "Sector C", "Phase 1"]],
    "road": [[v, 1] for v in ["Mall Road", "Canal Road"]],
})
