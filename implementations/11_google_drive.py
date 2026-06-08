"""
=============================================================
  11. Google Drive — File Sync with Chunking & Dedup
  Run: python 11_google_drive.py
  Implements block-level chunking, SHA-256 dedup, delta sync
  simulation, conflict resolution, and version history.
=============================================================
"""
import os
import hashlib
import time
import uuid
import json
from dataclasses import dataclass, field
from collections import defaultdict
from typing import Optional


CHUNK_SIZE = 64  # bytes (tiny for demo; production: 4-8 MB)


# ===================================================================
# Block Store (content-addressable)
# ===================================================================
class BlockStore:
    """Content-addressable block storage using SHA-256 hashes."""

    def __init__(self):
        self.blocks: dict[str, bytes] = {}  # hash -> data
        self.ref_counts: dict[str, int] = defaultdict(int)
        self._stats = {"stored": 0, "deduped": 0, "total_bytes": 0}

    def put(self, data: bytes) -> str:
        block_hash = hashlib.sha256(data).hexdigest()[:16]
        if block_hash in self.blocks:
            self.ref_counts[block_hash] += 1
            self._stats["deduped"] += 1
        else:
            self.blocks[block_hash] = data
            self.ref_counts[block_hash] = 1
            self._stats["stored"] += 1
            self._stats["total_bytes"] += len(data)
        return block_hash

    def get(self, block_hash: str) -> Optional[bytes]:
        return self.blocks.get(block_hash)

    def remove_ref(self, block_hash: str):
        self.ref_counts[block_hash] -= 1
        if self.ref_counts[block_hash] <= 0:
            self.blocks.pop(block_hash, None)
            del self.ref_counts[block_hash]


# ===================================================================
# File Metadata
# ===================================================================
@dataclass
class FileVersion:
    version_id: str
    block_hashes: list[str]
    size_bytes: int
    checksum: str  # whole-file hash
    created_at: float = field(default_factory=time.time)
    created_by: str = ""


@dataclass
class FileMetadata:
    file_id: str
    name: str
    path: str
    owner_id: str
    current_version: int = 0
    versions: list[FileVersion] = field(default_factory=list)
    shared_with: set = field(default_factory=set)
    is_deleted: bool = False
    created_at: float = field(default_factory=time.time)
    modified_at: float = field(default_factory=time.time)


# ===================================================================
# Chunker (fixed-size for simplicity; production uses CDC)
# ===================================================================
class Chunker:
    @staticmethod
    def chunk(data: bytes, chunk_size: int = CHUNK_SIZE) -> list[bytes]:
        return [data[i:i + chunk_size] for i in range(0, len(data), chunk_size)]

    @staticmethod
    def reassemble(block_store: BlockStore, block_hashes: list[str]) -> bytes:
        parts = []
        for h in block_hashes:
            block = block_store.get(h)
            if block:
                parts.append(block)
        return b"".join(parts)


# ===================================================================
# Delta Sync Engine
# ===================================================================
class DeltaSyncEngine:
    """Compares old and new block lists to compute minimal diff."""

    @staticmethod
    def compute_delta(old_hashes: list[str], new_hashes: list[str]) -> dict:
        old_set = set(old_hashes)
        new_set = set(new_hashes)

        unchanged = old_set & new_set
        added = new_set - old_set
        removed = old_set - new_set

        return {
            "unchanged": len(unchanged),
            "added": len(added),
            "removed": len(removed),
            "added_hashes": list(added),
            "removed_hashes": list(removed),
            "sync_ratio": len(added) / max(len(new_hashes), 1),
        }


# ===================================================================
# Conflict Resolver
# ===================================================================
class ConflictResolver:
    """Last-writer-wins with conflict copy for concurrent edits."""

    @staticmethod
    def resolve(local_version: FileVersion, remote_version: FileVersion,
                base_version: Optional[FileVersion]) -> tuple[str, Optional[FileVersion]]:
        """Returns (action, conflict_copy)."""
        if local_version.checksum == remote_version.checksum:
            return "no_conflict", None

        if base_version:
            if local_version.checksum == base_version.checksum:
                return "take_remote", None
            if remote_version.checksum == base_version.checksum:
                return "take_local", None

        # True conflict: keep both
        conflict_copy = FileVersion(
            version_id=f"conflict_{str(uuid.uuid4())[:6]}",
            block_hashes=remote_version.block_hashes,
            size_bytes=remote_version.size_bytes,
            checksum=remote_version.checksum,
            created_by=f"{remote_version.created_by}_conflict",
        )
        return "conflict_both_kept", conflict_copy


# ===================================================================
# Drive Service
# ===================================================================
class DriveService:
    def __init__(self):
        self.block_store = BlockStore()
        self.files: dict[str, FileMetadata] = {}  # file_id -> metadata
        self.path_index: dict[str, str] = {}  # path -> file_id
        self._stats = {"uploads": 0, "downloads": 0, "syncs": 0, "conflicts": 0}

    def upload(self, user_id: str, path: str, data: bytes) -> FileMetadata:
        """Upload file with chunking and dedup."""
        chunks = Chunker.chunk(data)
        block_hashes = [self.block_store.put(c) for c in chunks]
        file_checksum = hashlib.sha256(data).hexdigest()[:16]

        # Check if file exists at path
        file_id = self.path_index.get(path)
        if file_id and file_id in self.files:
            # Update existing file
            file_meta = self.files[file_id]
            version = FileVersion(
                version_id=str(uuid.uuid4())[:8],
                block_hashes=block_hashes,
                size_bytes=len(data),
                checksum=file_checksum,
                created_by=user_id,
            )
            file_meta.versions.append(version)
            file_meta.current_version = len(file_meta.versions) - 1
            file_meta.modified_at = time.time()
        else:
            # New file
            file_id = str(uuid.uuid4())[:8]
            version = FileVersion(
                version_id=str(uuid.uuid4())[:8],
                block_hashes=block_hashes,
                size_bytes=len(data),
                checksum=file_checksum,
                created_by=user_id,
            )
            file_meta = FileMetadata(
                file_id=file_id,
                name=os.path.basename(path),
                path=path,
                owner_id=user_id,
                versions=[version],
            )
            self.files[file_id] = file_meta
            self.path_index[path] = file_id

        self._stats["uploads"] += 1
        return file_meta

    def download(self, file_id: str, version: int = -1) -> Optional[bytes]:
        file_meta = self.files.get(file_id)
        if not file_meta:
            return None
        ver = file_meta.versions[version]
        self._stats["downloads"] += 1
        return Chunker.reassemble(self.block_store, ver.block_hashes)

    def sync(self, file_id: str, new_data: bytes, user_id: str) -> dict:
        """Delta sync: only upload changed blocks."""
        file_meta = self.files.get(file_id)
        if not file_meta:
            return {"error": "File not found"}

        old_version = file_meta.versions[file_meta.current_version]
        new_chunks = Chunker.chunk(new_data)
        new_hashes = [hashlib.sha256(c).hexdigest()[:16] for c in new_chunks]

        delta = DeltaSyncEngine.compute_delta(old_version.block_hashes, new_hashes)

        # Only store new blocks
        for i, (chunk, h) in enumerate(zip(new_chunks, new_hashes)):
            if h in delta["added_hashes"]:
                self.block_store.put(chunk)

        new_version = FileVersion(
            version_id=str(uuid.uuid4())[:8],
            block_hashes=new_hashes,
            size_bytes=len(new_data),
            checksum=hashlib.sha256(new_data).hexdigest()[:16],
            created_by=user_id,
        )
        file_meta.versions.append(new_version)
        file_meta.current_version = len(file_meta.versions) - 1
        file_meta.modified_at = time.time()
        self._stats["syncs"] += 1

        return {
            "delta": delta,
            "version": file_meta.current_version,
            "blocks_uploaded": delta["added"],
            "blocks_reused": delta["unchanged"],
        }

    def get_versions(self, file_id: str) -> list[dict]:
        file_meta = self.files.get(file_id)
        if not file_meta:
            return []
        return [
            {
                "version": i,
                "version_id": v.version_id,
                "size": v.size_bytes,
                "created_by": v.created_by,
                "blocks": len(v.block_hashes),
            }
            for i, v in enumerate(file_meta.versions)
        ]

    def share(self, file_id: str, user_id: str):
        file_meta = self.files.get(file_id)
        if file_meta:
            file_meta.shared_with.add(user_id)

    def stats(self) -> dict:
        return {
            **self._stats,
            "total_files": len(self.files),
            "block_store": self.block_store._stats,
            "unique_blocks": len(self.block_store.blocks),
            "storage_bytes": self.block_store._stats["total_bytes"],
        }


# ===================================================================
# Demo
# ===================================================================
if __name__ == "__main__":
    print("=" * 65)
    print("  Google Drive — File Sync with Chunking & Dedup")
    print(f"  Chunk size: {CHUNK_SIZE} bytes (demo)")
    print("=" * 65)

    drive = DriveService()

    # Upload a file
    content1 = b"Hello World! This is a system design document about file storage. " * 5
    print(f"\n  Uploading document.txt ({len(content1)} bytes)...")
    f1 = drive.upload("alice", "/docs/document.txt", content1)
    print(f"    File ID: {f1.file_id}, Blocks: {len(f1.versions[0].block_hashes)}")

    # Upload similar file (dedup test)
    content2 = b"Hello World! This is a system design document about file storage. " * 5
    content2 += b" EXTRA CONTENT ADDED HERE!"
    print(f"\n  Uploading document_v2.txt ({len(content2)} bytes, mostly same content)...")
    f2 = drive.upload("alice", "/docs/document_v2.txt", content2)

    print(f"  Block Store Stats: {drive.block_store._stats}")
    print(f"  Deduplication saved {drive.block_store._stats['deduped']} block stores")

    # Download and verify
    downloaded = drive.download(f1.file_id)
    print(f"\n  Downloaded document.txt: match={downloaded == content1}, size={len(downloaded)}")

    # Delta sync (modify file slightly)
    content_modified = content1[:100] + b"MODIFIED SECTION" + content1[116:]
    print(f"\n  Delta sync document.txt (small edit)...")
    sync_result = drive.sync(f1.file_id, content_modified, "alice")
    print(f"    Blocks uploaded: {sync_result['blocks_uploaded']}")
    print(f"    Blocks reused:   {sync_result['blocks_reused']}")
    print(f"    Sync ratio:      {sync_result['delta']['sync_ratio']:.1%} of blocks changed")

    # Version history
    print(f"\n  Version history for document.txt:")
    for v in drive.get_versions(f1.file_id):
        print(f"    v{v['version']}: {v['size']} bytes, {v['blocks']} blocks, by {v['created_by']}")

    # Conflict resolution demo
    print("\n  Conflict Resolution Demo:")
    base_ver = f1.versions[0]
    local_ver = FileVersion("local_1", base_ver.block_hashes + ["new_block_1"],
                            400, "check_local", created_by="alice")
    remote_ver = FileVersion("remote_1", base_ver.block_hashes + ["new_block_2"],
                             410, "check_remote", created_by="bob")

    action, conflict = ConflictResolver.resolve(local_ver, remote_ver, base_ver)
    print(f"    Both modified since base → action: {action}")
    if conflict:
        print(f"    Conflict copy created: {conflict.version_id}")

    # No-conflict case
    action2, _ = ConflictResolver.resolve(local_ver, local_ver, base_ver)
    print(f"    Same content → action: {action2}")

    # Share file
    drive.share(f1.file_id, "bob")
    print(f"\n  Shared document.txt with bob. Shared with: {f1.shared_with}")

    # Final stats
    print(f"\n  Drive Stats: {drive.stats()}")
    print("\nDone.")
