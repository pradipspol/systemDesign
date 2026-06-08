"""
=============================================================
  10. Video Streaming Platform — Transcoding Pipeline + HLS
  Run: python 10_video_streaming.py
  Simulates video upload, transcoding pipeline, HLS manifest
  generation, adaptive bitrate selection, and CDN edge caching.
=============================================================
"""
import os
import json
import time
import uuid
import hashlib
import threading
from enum import Enum
from dataclasses import dataclass, field
from collections import defaultdict
from typing import Optional


class VideoStatus(Enum):
    UPLOADED = "uploaded"
    QUEUED = "queued"
    TRANSCODING = "transcoding"
    READY = "ready"
    FAILED = "failed"


class Resolution(Enum):
    R_360P = ("360p", 640, 360, 800)      # (label, width, height, bitrate_kbps)
    R_480P = ("480p", 854, 480, 1400)
    R_720P = ("720p", 1280, 720, 2800)
    R_1080P = ("1080p", 1920, 1080, 5000)
    R_4K = ("4k", 3840, 2160, 15000)


@dataclass
class VideoSegment:
    segment_id: int
    duration: float  # seconds
    size_bytes: int
    resolution: str
    url: str


@dataclass
class VideoVariant:
    resolution: str
    width: int
    height: int
    bitrate_kbps: int
    segments: list[VideoSegment] = field(default_factory=list)
    total_size: int = 0


@dataclass
class Video:
    video_id: str
    title: str
    uploader_id: str
    duration_seconds: float
    original_size_mb: float
    status: VideoStatus = VideoStatus.UPLOADED
    variants: dict[str, VideoVariant] = field(default_factory=dict)
    thumbnail_url: str = ""
    created_at: float = field(default_factory=time.time)
    views: int = 0
    metadata: dict = field(default_factory=dict)


# ===================================================================
# Transcoding Pipeline
# ===================================================================
class TranscodingPipeline:
    SEGMENT_DURATION = 6.0  # seconds per HLS segment

    def __init__(self):
        self.queue: list[tuple[str, list[Resolution]]] = []
        self._stats = {"jobs_completed": 0, "jobs_failed": 0, "total_segments": 0}

    def submit(self, video_id: str, resolutions: list[Resolution] = None):
        if resolutions is None:
            resolutions = [Resolution.R_360P, Resolution.R_480P, Resolution.R_720P, Resolution.R_1080P]
        self.queue.append((video_id, resolutions))

    def process(self, video: Video) -> dict[str, VideoVariant]:
        """Simulate transcoding — generate segment metadata."""
        variants = {}
        num_segments = max(1, int(video.duration_seconds / self.SEGMENT_DURATION))

        for res in [Resolution.R_360P, Resolution.R_480P, Resolution.R_720P, Resolution.R_1080P]:
            label, width, height, bitrate = res.value
            variant = VideoVariant(
                resolution=label,
                width=width,
                height=height,
                bitrate_kbps=bitrate,
            )
            for i in range(num_segments):
                seg_size = int(bitrate * 1000 / 8 * self.SEGMENT_DURATION)
                segment = VideoSegment(
                    segment_id=i,
                    duration=self.SEGMENT_DURATION,
                    size_bytes=seg_size,
                    resolution=label,
                    url=f"/cdn/v/{video.video_id}/{label}/seg_{i:04d}.ts",
                )
                variant.segments.append(segment)
                variant.total_size += seg_size
                self._stats["total_segments"] += 1

            variants[label] = variant

        self._stats["jobs_completed"] += 1
        return variants


# ===================================================================
# HLS Manifest Generator
# ===================================================================
class HLSManifestGenerator:
    @staticmethod
    def master_playlist(video: Video) -> str:
        lines = ["#EXTM3U", "#EXT-X-VERSION:3", ""]
        for label, variant in sorted(video.variants.items(),
                                     key=lambda x: x[1].bitrate_kbps):
            lines.append(
                f"#EXT-X-STREAM-INF:BANDWIDTH={variant.bitrate_kbps * 1000},"
                f"RESOLUTION={variant.width}x{variant.height}"
            )
            lines.append(f"/cdn/v/{video.video_id}/{label}/playlist.m3u8")
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def variant_playlist(video: Video, resolution: str) -> str:
        variant = video.variants.get(resolution)
        if not variant:
            return ""
        lines = [
            "#EXTM3U",
            "#EXT-X-VERSION:3",
            f"#EXT-X-TARGETDURATION:{int(TranscodingPipeline.SEGMENT_DURATION)}",
            "#EXT-X-MEDIA-SEQUENCE:0",
            "",
        ]
        for seg in variant.segments:
            lines.append(f"#EXTINF:{seg.duration:.3f},")
            lines.append(seg.url)
        lines.append("#EXT-X-ENDLIST")
        return "\n".join(lines)


# ===================================================================
# CDN Edge Cache
# ===================================================================
class CDNEdgeCache:
    def __init__(self, name: str, capacity_mb: int = 1000):
        self.name = name
        self.capacity_bytes = capacity_mb * 1024 * 1024
        self.cache: dict[str, int] = {}  # url -> size_bytes
        self.used_bytes = 0
        self.hits = 0
        self.misses = 0

    def get(self, url: str) -> bool:
        if url in self.cache:
            self.hits += 1
            return True
        self.misses += 1
        return False

    def put(self, url: str, size_bytes: int):
        if url in self.cache:
            return
        while self.used_bytes + size_bytes > self.capacity_bytes and self.cache:
            evict_url = next(iter(self.cache))
            self.used_bytes -= self.cache.pop(evict_url)
        self.cache[url] = size_bytes
        self.used_bytes += size_bytes

    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0


# ===================================================================
# Adaptive Bitrate Selector
# ===================================================================
class ABRSelector:
    """Simulates client-side ABR: selects quality based on bandwidth."""

    @staticmethod
    def select(available_variants: dict[str, VideoVariant],
               bandwidth_kbps: float) -> Optional[str]:
        best = None
        for label, variant in available_variants.items():
            if variant.bitrate_kbps <= bandwidth_kbps * 0.8:  # 80% safety margin
                if best is None or variant.bitrate_kbps > available_variants[best].bitrate_kbps:
                    best = label
        return best or min(available_variants, key=lambda k: available_variants[k].bitrate_kbps)


# ===================================================================
# Video Platform Service
# ===================================================================
class VideoStreamingService:
    def __init__(self):
        self.videos: dict[str, Video] = {}
        self.pipeline = TranscodingPipeline()
        self.cdn = CDNEdgeCache("edge-us-west", capacity_mb=500)
        self._stats = {"uploads": 0, "views": 0}

    def upload(self, title: str, uploader_id: str, duration_s: float, size_mb: float) -> Video:
        video = Video(
            video_id=str(uuid.uuid4())[:8],
            title=title,
            uploader_id=uploader_id,
            duration_seconds=duration_s,
            original_size_mb=size_mb,
        )
        self.videos[video.video_id] = video
        self._stats["uploads"] += 1

        # Transcode
        video.status = VideoStatus.TRANSCODING
        video.variants = self.pipeline.process(video)
        video.status = VideoStatus.READY
        video.thumbnail_url = f"/cdn/thumb/{video.video_id}.jpg"

        # Pre-populate CDN for the first 3 segments of each variant
        for variant in video.variants.values():
            for seg in variant.segments[:3]:
                self.cdn.put(seg.url, seg.size_bytes)

        return video

    def stream(self, video_id: str, bandwidth_kbps: float) -> dict:
        video = self.videos.get(video_id)
        if not video or video.status != VideoStatus.READY:
            return {"error": "Video not available"}

        video.views += 1
        self._stats["views"] += 1

        selected = ABRSelector.select(video.variants, bandwidth_kbps)
        variant = video.variants[selected]

        # Simulate segment fetching
        cdn_hits = 0
        for seg in variant.segments:
            if self.cdn.get(seg.url):
                cdn_hits += 1
            else:
                self.cdn.put(seg.url, seg.size_bytes)

        return {
            "video_id": video_id,
            "title": video.title,
            "resolution": selected,
            "bitrate_kbps": variant.bitrate_kbps,
            "segments": len(variant.segments),
            "cdn_hit_rate": cdn_hits / len(variant.segments) if variant.segments else 0,
            "master_playlist": f"/cdn/v/{video_id}/master.m3u8",
        }


# ===================================================================
# Demo
# ===================================================================
if __name__ == "__main__":
    print("=" * 65)
    print("  Video Streaming — Transcoding + HLS + CDN + ABR")
    print("=" * 65)

    svc = VideoStreamingService()

    # Upload videos
    print("\n  Uploading videos...")
    v1 = svc.upload("System Design Interview Guide", "creator_1", 1800, 2048)
    v2 = svc.upload("Python in 10 Minutes", "creator_2", 600, 512)
    v3 = svc.upload("Live Concert 4K", "creator_3", 7200, 8192)

    for v in [v1, v2, v3]:
        sizes = {r: f"{var.total_size / 1024 / 1024:.1f} MB"
                 for r, var in v.variants.items()}
        print(f"    {v.title}: {v.duration_seconds}s, variants={sizes}")

    # HLS Manifests
    print(f"\n  Master Playlist for '{v1.title}':")
    print("  " + HLSManifestGenerator.master_playlist(v1).replace("\n", "\n  "))

    print(f"\n  Variant Playlist (720p) first 10 lines:")
    vpl = HLSManifestGenerator.variant_playlist(v1, "720p")
    for line in vpl.split("\n")[:10]:
        print(f"    {line}")
    print("    ...")

    # Stream with different bandwidths
    print("\n  Streaming with different bandwidths:")
    for bw in [500, 1500, 3500, 6000, 20000]:
        result = svc.stream(v1.video_id, bw)
        print(f"    {bw:6d} kbps → {result['resolution']:5s} "
              f"(bitrate={result['bitrate_kbps']}kbps, "
              f"CDN hit={result['cdn_hit_rate']:.0%})")

    # CDN stats
    print(f"\n  CDN '{svc.cdn.name}' stats:")
    print(f"    Cache entries: {len(svc.cdn.cache)}")
    print(f"    Used: {svc.cdn.used_bytes / 1024 / 1024:.1f} MB / "
          f"{svc.cdn.capacity_bytes / 1024 / 1024:.0f} MB")
    print(f"    Hit rate: {svc.cdn.hit_rate():.1%}")

    # Pipeline stats
    print(f"\n  Transcoding stats: {svc.pipeline._stats}")
    print(f"  Platform stats: {svc._stats}")
    print("\nDone.")
