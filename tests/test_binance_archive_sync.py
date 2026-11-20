from datetime import date
import json
import pytest
from binance_archive_sync import ArchiveError, archive_partition, discover_closed_partitions, sync_closed_partitions
from binance_stream_collector import HashChainJSONLStore

class FakeObjectStore:
    def __init__(self):
        self.uploads = []
    def upload_file(self, Filename, Bucket, Key, ExtraArgs=None):
        self.uploads.append((Filename, Bucket, Key, ExtraArgs))

def _partition(root, day="2026-11-19", market="spot", stream="trades"):
    store = HashChainJSONLStore(root, fsync_each_event=False)
    store.append(market, stream, {"t": 123, "p": "90000"},
                 received_at=f"{day}T10:00:00+00:00")
    store.close()
    return root / market / stream / f"{day}.jsonl"

def test_discovery_excludes_current_day_and_future(tmp_path):
    old = _partition(tmp_path, "2026-11-19")
    _partition(tmp_path, "2026-11-20", market="usdm")
    _partition(tmp_path, "2026-11-21", market="usdm")
    assert discover_closed_partitions(tmp_path, today=date(2026, 11, 20)) == [old]

def test_archive_verifies_and_uploads_content_addressed_object(tmp_path):
    path = _partition(tmp_path)
    client = FakeObjectStore()
    result = archive_partition(path, root=tmp_path, bucket="market-archive", client=client, prefix="raw")
    assert result.uploaded and result.event_count == 1
    assert result.object_key.startswith("raw/spot/trades/date=2026-11-19/sha256=")
    assert result.object_key.endswith(".jsonl")
    assert len(client.uploads) == 1
    assert client.uploads[0][1] == "market-archive"
    assert client.uploads[0][3]["Metadata"]["schema"] == "binance_raw_event_v1"

def test_archive_refuses_tampered_partition(tmp_path):
    path = _partition(tmp_path)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["payload"]["p"] = "1"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    client = FakeObjectStore()
    with pytest.raises(ArchiveError, match="hash chain"):
        archive_partition(path, root=tmp_path, bucket="market-archive", client=client)
    assert client.uploads == []

def test_archive_refuses_path_outside_data_root(tmp_path):
    outside = tmp_path.parent / "outside.jsonl"
    outside.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ArchiveError, match="inside"):
        archive_partition(outside, root=tmp_path, bucket="b", client=FakeObjectStore())

def test_dry_run_does_not_upload(tmp_path):
    path = _partition(tmp_path)
    client = FakeObjectStore()
    result = archive_partition(path, root=tmp_path, bucket="b", client=client, dry_run=True)
    assert not result.uploaded and client.uploads == []

def test_sync_is_repeatable_and_separates_market_namespaces(tmp_path):
    _partition(tmp_path, market="spot")
    _partition(tmp_path, market="usdm")
    client = FakeObjectStore()
    results = sync_closed_partitions(tmp_path, bucket="b", client=client, today=date(2026, 11, 20))
    assert len(results) == 2
    assert {item.object_key.split("/")[2] for item in results} == {"spot", "usdm"}
