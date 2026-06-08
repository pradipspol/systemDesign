# 10. Video Streaming Platform (YouTube / Netflix)

> **Difficulty**: Hard | **Asked by**: Netflix, Google, Amazon, Meta, Disney+

## Table of Contents
- [Requirements](#requirements)
- [Capacity Estimation](#capacity-estimation)
- [High-Level Design](#high-level-design)
- [Low-Level Design](#low-level-design)
- [Implementation](#implementation)
- [Limitations & Improvements](#limitations--improvements)

---

## Requirements

### Functional Requirements
1. Upload videos (creators)
2. Stream/watch videos (viewers)
3. Search videos by title, description, tags
4. Video recommendations
5. Like, comment, subscribe
6. Video analytics (views, watch time)

### Non-Functional Requirements
1. **High Availability**: 99.99% uptime
2. **Low Latency Streaming**: < 200ms start time
3. **Scalability**: 1B DAU, 500M hours of video watched/day
4. **Global Reach**: Low latency worldwide via CDN
5. **Adaptive Bitrate**: Adjust quality based on bandwidth

---

## Capacity Estimation

```
DAU: 1 Billion users
Videos watched/day: 5B (avg 5 per user)
New videos uploaded/day: 500K
Average video length: 5 minutes
Average video size (raw): 600 MB (1080p, 5 min)
Storage per video (all encodings): ~2 GB
Daily upload storage: 500K × 2 GB = 1 PB/day
Total storage (5 years): ~1.8 Exabytes

Streaming bandwidth:
  5B videos × 10 MB average (adaptive) = 50 PB/day
  Peak bandwidth: ~5 Tbps

CDN: Cache 20% of videos (popular) = covers 80% of traffic
```

---

## High-Level Design

### Architecture Overview

```mermaid
graph TB
    subgraph "Upload Path"
        Creator[Creator] --> Upload[Upload Service]
        Upload --> OrigStore[(Original Store<br/>S3)]
        Upload --> MQ[(Message Queue)]
        MQ --> Transcode[Transcoding Service<br/>FFmpeg Workers]
        Transcode --> TransStore[(Transcoded Store<br/>S3)]
        Transcode --> Thumb[Thumbnail<br/>Generator]
        TransStore --> CDN[CDN<br/>CloudFront/Akamai]
    end
    
    subgraph "Streaming Path"
        Viewer[Viewer] --> CDN
        CDN -->|Cache Miss| TransStore
        Viewer --> API[API Gateway]
        API --> VideoSvc[Video Metadata Svc]
        VideoSvc --> MetaDB[(Metadata DB<br/>MySQL)]
        VideoSvc --> SearchSvc[Search Service<br/>Elasticsearch]
    end
    
    subgraph "Supporting Services"
        RecSvc[Recommendation<br/>Service]
        AnalyticsSvc[Analytics Service]
        CommentSvc[Comment Service]
        SubSvc[Subscription Service]
    end
    
    API --> RecSvc & AnalyticsSvc & CommentSvc & SubSvc
```

### Video Upload & Processing Pipeline

```mermaid
sequenceDiagram
    participant C as Creator
    participant API as Upload API
    participant S3 as S3 (Original)
    participant Q as Task Queue
    participant T as Transcoder
    participant CDN as CDN
    participant DB as Metadata DB
    
    C->>API: Upload video (multipart)
    API->>S3: Store original file
    API->>DB: Create video record (status: processing)
    API->>Q: Enqueue transcoding job
    API-->>C: 202 Accepted (video_id)
    
    Q->>T: Dequeue job
    
    par Parallel Transcoding
        T->>T: Encode 240p (H.264)
        T->>T: Encode 360p (H.264)
        T->>T: Encode 480p (H.264)
        T->>T: Encode 720p (H.264)
        T->>T: Encode 1080p (H.264)
        T->>T: Encode 4K (H.265)
    end
    
    T->>T: Generate thumbnails
    T->>T: Extract audio track
    T->>T: Generate subtitles (speech-to-text)
    T->>S3: Store all variants
    T->>CDN: Pre-warm popular regions
    T->>DB: Update status: ready
    T-->>C: Notification: Video is live
```

### Adaptive Bitrate Streaming (ABR)

```mermaid
graph LR
    subgraph "Video Segments"
        V["Original Video"] --> Split["Split into<br/>4-second segments"]
        Split --> S1["Segment 1"]
        Split --> S2["Segment 2"]
        Split --> S3["Segment N"]
    end
    
    subgraph "Each Segment Encoded Multiple Times"
        S1 --> E1["240p - 300kbps"]
        S1 --> E2["480p - 1Mbps"]
        S1 --> E3["720p - 3Mbps"]
        S1 --> E4["1080p - 6Mbps"]
        S1 --> E5["4K - 16Mbps"]
    end
    
    subgraph "Client Player"
        Monitor["Bandwidth Monitor"] --> Switch["Quality Switcher"]
        Switch -->|High bandwidth| E4
        Switch -->|Low bandwidth| E2
        Switch -->|Bandwidth drop| E1
    end
```

### HLS/DASH Manifest

```
#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=300000,RESOLUTION=426x240
/video/abc123/240p/playlist.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=854x480
/video/abc123/480p/playlist.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1280x720
/video/abc123/720p/playlist.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=6000000,RESOLUTION=1920x1080
/video/abc123/1080p/playlist.m3u8

# Segment playlist (720p):
#EXTINF:4.0,
segment_001.ts
#EXTINF:4.0,
segment_002.ts
...
```

---

## Low-Level Design

### Data Models

```mermaid
erDiagram
    VIDEO {
        bigint id PK
        bigint creator_id FK
        varchar title
        text description
        varchar status "processing|ready|failed|deleted"
        int duration_seconds
        bigint view_count
        bigint like_count
        varchar thumbnail_url
        jsonb encoding_variants "list of resolutions + URLs"
        timestamp published_at
        timestamp created_at
    }
    
    VIDEO_SEGMENT {
        bigint id PK
        bigint video_id FK
        int segment_number
        varchar resolution "240p|480p|720p|1080p|4k"
        varchar codec "h264|h265|vp9|av1"
        varchar url
        int size_bytes
        float duration_seconds
    }
    
    VIEW_EVENT {
        bigint id PK
        bigint video_id FK
        bigint user_id
        int watch_duration_seconds
        varchar quality
        varchar country
        timestamp watched_at
    }
    
    COMMENT {
        bigint id PK
        bigint video_id FK
        bigint user_id FK
        bigint parent_id "for replies"
        text content
        int like_count
        timestamp created_at
    }
    
    SUBSCRIPTION {
        bigint subscriber_id PK
        bigint channel_id PK
        boolean notifications_enabled
        timestamp created_at
    }
    
    VIDEO ||--|{ VIDEO_SEGMENT : has
    VIDEO ||--|{ VIEW_EVENT : generates
    VIDEO ||--|{ COMMENT : has
```

### CDN Architecture

```mermaid
graph TB
    subgraph "CDN Multi-tier"
        Client[Client] --> Edge[Edge PoP<br/>100+ global locations<br/>SSD Cache]
        Edge -->|Miss| Regional[Regional Cache<br/>~20 locations<br/>Large SSD + HDD]
        Regional -->|Miss| Origin[Origin<br/>S3 + Origin Shield]
    end
    
    subgraph "Cache Strategy"
        Popular["Popular videos (top 5%)<br/>Cached everywhere<br/>Hit rate: 80%"]
        Medium["Medium popularity<br/>Cached at regional<br/>Hit rate: 15%"]
        LongTail["Long tail<br/>Origin only<br/>Hit rate: 5%"]
    end
```

### Transcoding Pipeline Architecture

```mermaid
flowchart TD
    Upload[Video Upload] --> Validate[Validate Format<br/>Check codec, duration, size]
    Validate --> Split[Split into chunks<br/>for parallel processing]
    Split --> C1[Chunk 1]
    Split --> C2[Chunk 2]
    Split --> CN[Chunk N]
    
    C1 --> W1[Worker: Encode<br/>all resolutions]
    C2 --> W2[Worker: Encode<br/>all resolutions]
    CN --> WN[Worker: Encode<br/>all resolutions]
    
    W1 & W2 & WN --> Merge[Merge chunks<br/>per resolution]
    Merge --> Package[Package as<br/>HLS/DASH]
    Package --> QC[Quality Check<br/>VMAF score]
    QC --> Store[Store to S3<br/>+ CDN Pre-warm]
```

### Video Recommendation System

```mermaid
flowchart TD
    subgraph "Candidate Generation"
        UserHistory[User Watch History] --> CF[Collaborative Filtering<br/>Users who watched X also watched Y]
        UserProfile[User Profile] --> CB[Content-Based<br/>Similar titles, tags, categories]
        Trending[Trending Videos] --> Pop[Popularity-Based]
    end
    
    CF & CB & Pop --> Merge[Merge Candidates<br/>~1000 videos]
    
    subgraph "Ranking"
        Merge --> Features[Feature Extraction:<br/>- Watch time prediction<br/>- Click probability<br/>- User engagement score<br/>- Video freshness]
        Features --> Model[Deep Neural Network<br/>Ranking Model]
        Model --> TopN[Top 50 ranked videos]
    end
    
    subgraph "Re-ranking"
        TopN --> Diversity[Diversity Filter<br/>Avoid similar content]
        Diversity --> Business[Business Rules<br/>Ads, promoted content]
        Business --> Final[Final 20 recommendations]
    end
```

---

## Implementation

### Video Upload Service

```python
import uuid
from dataclasses import dataclass
from typing import List, Optional
from enum import Enum

class VideoStatus(Enum):
    UPLOADING = "uploading"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"

@dataclass
class TranscodingJob:
    video_id: str
    source_url: str
    target_resolutions: List[str]
    codec: str = "h264"
    
class VideoUploadService:
    """Handles video upload and transcoding orchestration."""
    
    SUPPORTED_FORMATS = {"mp4", "mov", "avi", "mkv", "webm"}
    MAX_FILE_SIZE = 128 * 1024 * 1024 * 1024  # 128 GB
    RESOLUTIONS = ["240p", "360p", "480p", "720p", "1080p"]
    
    def __init__(self, storage, queue, metadata_db, cdn):
        self.storage = storage
        self.queue = queue
        self.db = metadata_db
        self.cdn = cdn
    
    async def upload(self, creator_id: int, file_stream, 
                     metadata: dict) -> str:
        video_id = str(uuid.uuid4())
        
        # 1. Validate
        self._validate(file_stream, metadata)
        
        # 2. Upload to S3 (multipart for large files)
        source_url = await self.storage.upload(
            bucket="raw-videos",
            key=f"{video_id}/original",
            stream=file_stream
        )
        
        # 3. Create metadata record
        await self.db.create_video({
            "id": video_id,
            "creator_id": creator_id,
            "title": metadata["title"],
            "description": metadata.get("description", ""),
            "status": VideoStatus.PROCESSING.value,
            "source_url": source_url
        })
        
        # 4. Enqueue transcoding job
        job = TranscodingJob(
            video_id=video_id,
            source_url=source_url,
            target_resolutions=self.RESOLUTIONS
        )
        await self.queue.enqueue("transcoding-jobs", job)
        
        return video_id
    
    def _validate(self, file_stream, metadata):
        if not metadata.get("title"):
            raise ValueError("Title required")
        # Additional validations...


class VideoStreamingService:
    """Serves video streaming requests."""
    
    def __init__(self, metadata_db, cdn_url_signer):
        self.db = metadata_db
        self.signer = cdn_url_signer
    
    async def get_manifest(self, video_id: str, 
                           user_id: Optional[int] = None) -> dict:
        """Return HLS/DASH manifest with signed CDN URLs."""
        video = await self.db.get_video(video_id)
        
        if video["status"] != "ready":
            raise ValueError("Video not available")
        
        # Generate signed URLs (expire in 6 hours)
        variants = []
        for variant in video["encoding_variants"]:
            signed_url = self.signer.sign(
                url=variant["url"],
                expires_in=21600  # 6 hours
            )
            variants.append({
                "resolution": variant["resolution"],
                "bitrate": variant["bitrate"],
                "codec": variant["codec"],
                "url": signed_url
            })
        
        # Log view event (async)
        await self._log_view(video_id, user_id)
        
        return {
            "video_id": video_id,
            "title": video["title"],
            "duration": video["duration_seconds"],
            "variants": variants
        }
```

---

## Limitations & Improvements

### Current Limitations

| Limitation | Impact | Severity |
|-----------|--------|----------|
| Transcoding cost & time | Hours for 4K videos, expensive GPU | High |
| CDN costs at petabyte scale | Major infrastructure expense | High |
| Cold start for new videos | Not cached at edge, slow first view | Medium |
| Copyright detection latency | Infringing content visible briefly | Medium |
| Live streaming not covered | Different architecture needed | Medium |

### Improvement Areas

1. **Codec Evolution** — AV1 codec for 30% better compression than H.265
2. **Edge Computing** — Transcode at edge for live streams
3. **AI-Powered CDN** — Predict popular videos and pre-warm cache
4. **Content Moderation** — Real-time AI moderation during upload
5. **Live Streaming** — WebRTC for ultra-low latency; HLS for scale

---

## Key Interview Discussion Points

1. **Why HLS/DASH over progressive download?** Adaptive bitrate, better seeking, CDN-friendly segments
2. **How does Netflix handle 200M users?** Open Connect CDN (custom hardware at ISPs)
3. **Horizontal scaling for transcoding?** Spot instances, GPU clusters, chunked parallel processing
4. **How to handle viral videos?** CDN auto-scales; origin shield prevents thundering herd
5. **Cost optimization?** Tiered storage (hot/cold), codec efficiency, regional transcoding
