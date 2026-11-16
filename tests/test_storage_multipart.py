"""Tests for enterprise artifact storage backend and multipart uploads."""
from __future__ import annotations

import hashlib
import pytest

from otternest.storage import LocalStorageBackend, S3StorageBackend, StorageError


def test_local_storage_single_file_lifecycle(tmp_path):
    backend = LocalStorageBackend(base_dir=tmp_path)
    content = b"Single artifact binary data payload"
    uri = backend.save("proj-1", "art-1", content)
    assert (tmp_path / "proj-1" / "art-1").is_file()
    assert backend.load(uri) == content

    backend.delete(uri)
    assert not (tmp_path / "proj-1" / "art-1").exists()


def test_local_storage_multipart_upload_lifecycle(tmp_path):
    backend = LocalStorageBackend(base_dir=tmp_path)
    part1 = b"Part 1 chunk: Large dataset start\n"
    part2 = b"Part 2 chunk: Middle contents here\n"
    part3 = b"Part 3 chunk: Trailing end of large artifact\n"

    upload_id = backend.initiate_multipart("proj-1", "large-art-1", "dataset.tar.gz")

    p1_info = backend.upload_part(upload_id, 1, part1)
    p2_info = backend.upload_part(upload_id, 2, part2)
    p3_info = backend.upload_part(upload_id, 3, part3)

    assert p1_info["part_number"] == 1
    assert p1_info["sha256"] == hashlib.sha256(part1).hexdigest()

    # Complete multipart in disordered input list to verify auto-sort
    uri, final_sha, total_size = backend.complete_multipart(
        upload_id,
        [p3_info, p1_info, p2_info],
    )

    full_expected = part1 + part2 + part3
    assert total_size == len(full_expected)
    assert final_sha == hashlib.sha256(full_expected).hexdigest()
    assert backend.load(uri) == full_expected


def test_local_storage_multipart_checksum_mismatch(tmp_path):
    backend = LocalStorageBackend(base_dir=tmp_path)
    part1 = b"chunk 1"
    upload_id = backend.initiate_multipart("proj-1", "art-err", "file.bin")
    p1_info = backend.upload_part(upload_id, 1, part1)

    # Corrupt sha in part completion
    corrupt_info = dict(p1_info)
    corrupt_info["sha256"] = "0" * 64

    with pytest.raises(StorageError, match="checksum verification failed"):
        backend.complete_multipart(upload_id, [corrupt_info])


def test_s3_storage_backend_instantiation():
    s3 = S3StorageBackend(bucket="my-bucket", endpoint_url="https://minio.local:9000")
    uri = s3.save("proj-x", "art-y", b"content")
    assert uri == "s3://my-bucket/proj-x/art-y"
