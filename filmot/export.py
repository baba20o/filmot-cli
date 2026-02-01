"""Export functionality for Filmot CLI results."""

import json
import csv
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime


def export_json(data: Dict[str, Any], filepath: str, pretty: bool = True) -> str:
    """
    Export data to JSON file.
    
    Args:
        data: Data to export
        filepath: Output file path
        pretty: Pretty print with indentation
    
    Returns:
        Path to the created file
    """
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(path, 'w', encoding='utf-8') as f:
        if pretty:
            json.dump(data, f, indent=2, ensure_ascii=False)
        else:
            json.dump(data, f, ensure_ascii=False)
    
    return str(path)


def export_csv(data: Dict[str, Any], filepath: str, result_type: str = "subtitles") -> str:
    """
    Export search results to CSV file.
    
    Args:
        data: API response data
        filepath: Output file path
        result_type: Type of results ("subtitles", "videos", "channels")
    
    Returns:
        Path to the created file
    """
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    
    if result_type == "subtitles":
        return _export_subtitles_csv(data, path)
    elif result_type == "videos":
        return _export_videos_csv(data, path)
    elif result_type == "channels":
        return _export_channels_csv(data, path)
    else:
        raise ValueError(f"Unknown result type: {result_type}")


def _export_subtitles_csv(data: Dict[str, Any], path: Path) -> str:
    """Export subtitle search results to CSV."""
    videos = data.get("result", data.get("videos", data.get("items", [])))
    
    fieldnames = [
        "video_id", "title", "channel_name", "channel_id", 
        "views", "likes", "duration_seconds", "category",
        "language", "upload_date", "channel_subs", "channel_country",
        "video_url", "channel_url", "hit_count", "hits_text"
    ]
    
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        
        for video in videos:
            hits = video.get("hits", [])
            hits_text = _extract_hits_text(hits)
            
            row = {
                "video_id": video.get("id", ""),
                "title": video.get("title", ""),
                "channel_name": video.get("channelname", ""),
                "channel_id": video.get("channelid", ""),
                "views": video.get("viewcount", 0),
                "likes": video.get("likecount", 0),
                "duration_seconds": video.get("duration", 0),
                "category": video.get("category", ""),
                "language": video.get("lang", ""),
                "upload_date": video.get("uploaddate", ""),
                "channel_subs": video.get("channelsubcount", 0),
                "channel_country": video.get("channelcountryname", ""),
                "video_url": f"https://youtube.com/watch?v={video.get('id', '')}",
                "channel_url": f"https://youtube.com/channel/{video.get('channelid', '')}",
                "hit_count": len(hits),
                "hits_text": hits_text
            }
            writer.writerow(row)
    
    return str(path)


def _export_videos_csv(data: Dict[str, Any], path: Path) -> str:
    """Export video metadata to CSV."""
    videos = data if isinstance(data, list) else [data]
    
    fieldnames = [
        "video_id", "title", "channel_name", "channel_id",
        "duration_seconds", "upload_date", "video_url", "channel_url"
    ]
    
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        
        for video in videos:
            row = {
                "video_id": video.get("id", ""),
                "title": video.get("title", ""),
                "channel_name": video.get("channelname", ""),
                "channel_id": video.get("channelid", ""),
                "duration_seconds": video.get("duration", 0),
                "upload_date": video.get("uploaddate", ""),
                "video_url": f"https://youtube.com/watch?v={video.get('id', '')}",
                "channel_url": f"https://youtube.com/channel/{video.get('channelid', '')}"
            }
            writer.writerow(row)
    
    return str(path)


def _export_channels_csv(data: Dict[str, Any], path: Path) -> str:
    """Export channel search results to CSV."""
    channels = data if isinstance(data, list) else []
    
    fieldnames = [
        "channel_id", "channel_name", "handle", "subscribers",
        "total_views", "channel_url"
    ]
    
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        
        for channel in channels:
            row = {
                "channel_id": channel.get("value", ""),
                "channel_name": channel.get("label", ""),
                "handle": channel.get("newshortname", ""),
                "subscribers": channel.get("subcount", 0),
                "total_views": channel.get("viewcount", 0),
                "channel_url": f"https://youtube.com/channel/{channel.get('value', '')}"
            }
            writer.writerow(row)
    
    return str(path)


def _extract_hits_text(hits: List[Dict]) -> str:
    """Extract and concatenate all hit text from subtitle matches."""
    texts = []
    for hit in hits:
        # Handle hit_format=1 (lines array)
        lines = hit.get("lines", [])
        if lines:
            for line in lines:
                texts.append(line.get("text", ""))
        else:
            # Handle hit_format=0 (context snippets)
            token = hit.get("token", "")
            ctx_before = hit.get("ctx_before", "")
            ctx_after = hit.get("ctx_after", "")
            texts.append(f"{ctx_before} {token} {ctx_after}".strip())
    
    return " | ".join(texts)


def export_hits_detailed(data: Dict[str, Any], filepath: str) -> str:
    """
    Export subtitle hits in detailed format (one row per hit).
    
    Args:
        data: API response data
        filepath: Output file path
    
    Returns:
        Path to the created file
    """
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    
    videos = data.get("result", data.get("videos", data.get("items", [])))
    
    fieldnames = [
        "video_id", "title", "channel_name", "timestamp_seconds",
        "timestamp_formatted", "hit_text", "video_url_at_time"
    ]
    
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        
        for video in videos:
            video_id = video.get("id", "")
            title = video.get("title", "")
            channel = video.get("channelname", "")
            
            for hit in video.get("hits", []):
                start = hit.get("start", 0)
                
                # Handle both hit formats
                lines = hit.get("lines", [])
                if lines:
                    for line in lines:
                        line_start = line.get("start", start)
                        text = line.get("text", "")
                        _write_hit_row(writer, video_id, title, channel, line_start, text)
                else:
                    token = hit.get("token", "")
                    ctx_before = hit.get("ctx_before", "")
                    ctx_after = hit.get("ctx_after", "")
                    text = f"{ctx_before} {token} {ctx_after}".strip()
                    _write_hit_row(writer, video_id, title, channel, start, text)
    
    return str(path)


def _write_hit_row(writer, video_id, title, channel, timestamp, text):
    """Write a single hit row to CSV."""
    mins, secs = divmod(int(timestamp), 60)
    hours, mins = divmod(mins, 60)
    if hours:
        ts_formatted = f"{hours}:{mins:02d}:{secs:02d}"
    else:
        ts_formatted = f"{mins}:{secs:02d}"
    
    row = {
        "video_id": video_id,
        "title": title,
        "channel_name": channel,
        "timestamp_seconds": timestamp,
        "timestamp_formatted": ts_formatted,
        "hit_text": text,
        "video_url_at_time": f"https://youtube.com/watch?v={video_id}&t={int(timestamp)}"
    }
    writer.writerow(row)


def generate_filename(prefix: str, extension: str) -> str:
    """Generate a timestamped filename."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{timestamp}.{extension}"
