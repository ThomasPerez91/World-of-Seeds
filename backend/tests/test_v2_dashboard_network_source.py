from __future__ import annotations

from app.integrations.prometheus_network import _network_query


def test_network_query_prefers_cadvisor_root_namespace_with_node_fallback() -> None:
    receive = _network_query("receive")
    transmit = _network_query("transmit")

    for query, direction in ((receive, "receive"), (transmit, "transmit")):
        assert f"container_network_{direction}_bytes_total" in query
        assert 'job="cadvisor"' in query
        assert 'id="/"' in query
        assert "label_replace(" in query
        assert '"device","$1","interface","(.*)"' in query
        assert f"node_network_{direction}_bytes_total" in query
        assert 'job="node-exporter"' in query
        assert "unless on()" in query
        assert "max by (device)" in query
        assert "irate(" in query
