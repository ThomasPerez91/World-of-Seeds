from app.observability.torrent_metrics_exporter import (
    _escape_label,
    _newgreedy_lines,
    _qb_lines,
)


def test_escape_label_is_prometheus_safe() -> None:
    assert _escape_label('a"b\\c\n') == 'a\\"b\\\\c\\n'


def test_qb_lines_export_bounded_hash_state_and_rates() -> None:
    info_hash = "a" * 40
    lines = _qb_lines(
        [
            {
                "hash": info_hash,
                "state": "downloading",
                "progress": 0.5,
                "dlspeed": 1024,
                "upspeed": 256,
                "downloaded": 4096,
                "uploaded": 512,
            }
        ],
        {
            "dl_info_speed": 1024,
            "up_info_speed": 256,
            "dl_info_data": 4096,
            "up_info_data": 512,
        },
        truncated=False,
    )
    payload = "\n".join(lines)

    assert 'wos_torrent_qb_progress_ratio{hash8="aaaaaaaa",state="downloading"} 0.5' in payload
    assert 'wos_torrent_qb_torrents{state="downloading"} 1' in payload
    assert "wos_torrent_qb_active_torrents 1" in payload
    assert "wos_torrent_qb_global_download_rate_bytes_per_second 1024.0" in payload
    assert info_hash not in payload


def test_newgreedy_lines_export_tracker_and_hash_status() -> None:
    lines = _newgreedy_lines(
        {
            "_schema_version": 4,
            "_tracker_cumul": {"c411.org": {"ul": 120143052450.0, "dl": 156419387570.0}},
            "76c0446e": {
                "cumul_rep_ul": 120143052450.0,
                "cumul_rep_dl": 31283877514,
                "cumul_real_ul": 0.0,
                "ann_count": 6,
                "stalled": False,
                "target_reached": False,
                "mode": "seed",
                "last_announce_ts": 1788886069.0,
            },
        }
    )
    payload = "\n".join(lines)

    assert (
        'wos_newgreedy_torrent_status{hash8="76c0446e",mode="seed",stalled="false",'
        'target_reached="false"} 1'
    ) in payload
    assert (
        'wos_newgreedy_tracker_reported_bytes{direction="upload",tracker="c411.org"} 120143052450.0'
    ) in payload
    assert 'wos_newgreedy_torrent_announces_total{hash8="76c0446e"} 6.0' in payload
    assert "wos_newgreedy_stalled_torrents 0" in payload
    assert "wos_newgreedy_target_reached_torrents 0" in payload
