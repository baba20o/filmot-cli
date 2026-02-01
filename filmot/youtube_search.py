"""
YouTube Data API search for finding recent videos.

This module fills the gap when Filmot hasn't indexed recent content yet.
Uses YouTube Data API v3 for video search, then leverages our existing
transcript.py for fetching captions.
"""

import os
import requests
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv

load_dotenv()

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"


def validate_youtube_api():
    """Check if YouTube API key is configured."""
    if not YOUTUBE_API_KEY:
        raise ValueError(
            "Missing YOUTUBE_API_KEY in .env file. "
            "Get one from https://console.cloud.google.com/apis/credentials"
        )
    return True


def search_videos(
    query: str,
    max_results: int = 25,
    published_after: Optional[datetime] = None,
    published_before: Optional[datetime] = None,
    order: str = "date",  # date, rating, relevance, title, viewCount
    channel_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Search YouTube for videos matching a query.
    
    Args:
        query: Search terms (supports YouTube search operators)
        max_results: Maximum number of results (1-50)
        published_after: Only videos published after this date
        published_before: Only videos published before this date
        order: Sort order - date, rating, relevance, title, viewCount
        channel_id: Limit search to specific channel
        
    Returns:
        List of video metadata dictionaries
    """
    validate_youtube_api()
    
    params = {
        "key": YOUTUBE_API_KEY,
        "q": query,
        "part": "snippet",
        "type": "video",
        "maxResults": min(max_results, 50),
        "order": order,
    }
    
    if published_after:
        params["publishedAfter"] = published_after.strftime("%Y-%m-%dT%H:%M:%SZ")
    if published_before:
        params["publishedBefore"] = published_before.strftime("%Y-%m-%dT%H:%M:%SZ")
    if channel_id:
        params["channelId"] = channel_id
    
    response = requests.get(YOUTUBE_SEARCH_URL, params=params)
    response.raise_for_status()
    data = response.json()
    
    videos = []
    for item in data.get("items", []):
        snippet = item.get("snippet", {})
        videos.append({
            "video_id": item["id"]["videoId"],
            "title": snippet.get("title", ""),
            "description": snippet.get("description", ""),
            "channel_title": snippet.get("channelTitle", ""),
            "channel_id": snippet.get("channelId", ""),
            "published_at": snippet.get("publishedAt", ""),
            "thumbnail": snippet.get("thumbnails", {}).get("high", {}).get("url", ""),
            "url": f"https://youtube.com/watch?v={item['id']['videoId']}",
        })
    
    return videos


def get_video_details(video_ids: List[str]) -> List[Dict[str, Any]]:
    """
    Get detailed metadata for specific videos (views, duration, etc).
    
    Args:
        video_ids: List of YouTube video IDs
        
    Returns:
        List of detailed video metadata
    """
    validate_youtube_api()
    
    # API accepts up to 50 IDs at once
    params = {
        "key": YOUTUBE_API_KEY,
        "id": ",".join(video_ids[:50]),
        "part": "snippet,statistics,contentDetails",
    }
    
    response = requests.get(YOUTUBE_VIDEOS_URL, params=params)
    response.raise_for_status()
    data = response.json()
    
    videos = []
    for item in data.get("items", []):
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        content = item.get("contentDetails", {})
        
        videos.append({
            "video_id": item["id"],
            "title": snippet.get("title", ""),
            "description": snippet.get("description", ""),
            "channel_title": snippet.get("channelTitle", ""),
            "channel_id": snippet.get("channelId", ""),
            "published_at": snippet.get("publishedAt", ""),
            "thumbnail": snippet.get("thumbnails", {}).get("high", {}).get("url", ""),
            "url": f"https://youtube.com/watch?v={item['id']}",
            "views": int(stats.get("viewCount", 0)),
            "likes": int(stats.get("likeCount", 0)),
            "comments": int(stats.get("commentCount", 0)),
            "duration": content.get("duration", ""),  # ISO 8601 format
        })
    
    return videos


def search_recent(
    query: str,
    days_back: int = 7,
    max_results: int = 25,
    order: str = "date",
) -> List[Dict[str, Any]]:
    """
    Convenience function to search for recent videos.
    
    Args:
        query: Search terms
        days_back: How many days back to search
        max_results: Maximum results
        order: Sort order
        
    Returns:
        List of video metadata with full details
    """
    published_after = datetime.utcnow() - timedelta(days=days_back)
    
    # First get search results
    search_results = search_videos(
        query=query,
        max_results=max_results,
        published_after=published_after,
        order=order,
    )
    
    if not search_results:
        return []
    
    # Then enrich with full details (views, duration, etc)
    video_ids = [v["video_id"] for v in search_results]
    detailed = get_video_details(video_ids)
    
    return detailed


def format_duration(iso_duration: str) -> str:
    """Convert ISO 8601 duration to human readable format."""
    import re
    match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', iso_duration)
    if not match:
        return iso_duration
    
    hours, minutes, seconds = match.groups()
    hours = int(hours) if hours else 0
    minutes = int(minutes) if minutes else 0
    seconds = int(seconds) if seconds else 0
    
    if hours:
        return f"{hours}h {minutes}m"
    elif minutes:
        return f"{minutes}m {seconds}s"
    else:
        return f"{seconds}s"
