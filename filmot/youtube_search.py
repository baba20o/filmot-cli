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
    order: str = "date",
    channel_id: Optional[str] = None,
    region_code: Optional[str] = None,
    relevance_language: Optional[str] = None,
    safe_search: Optional[str] = None,
    video_caption: Optional[str] = None,
    video_category_id: Optional[str] = None,
    video_definition: Optional[str] = None,
    video_dimension: Optional[str] = None,
    video_duration: Optional[str] = None,
    video_embeddable: Optional[str] = None,
    video_license: Optional[str] = None,
    video_syndicated: Optional[str] = None,
    video_type: Optional[str] = None,
    event_type: Optional[str] = None,
    location: Optional[str] = None,
    location_radius: Optional[str] = None,
    topic_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Search YouTube for videos matching a query with full API parameter support.
    
    Args:
        query: Search terms (supports YouTube search operators)
        max_results: Maximum number of results (1-50)
        published_after: Only videos published after this date
        published_before: Only videos published before this date
        order: Sort order - date, rating, relevance, title, viewCount
        channel_id: Limit search to specific channel
        region_code: ISO 3166-1 alpha-2 country code (e.g., 'US', 'GB')
        relevance_language: ISO 639-1 language code for relevance ranking
        safe_search: 'none', 'moderate', 'strict'
        video_caption: 'any', 'closedCaption', 'none'
        video_category_id: YouTube video category ID
        video_definition: 'any', 'high' (HD), 'standard' (SD)
        video_dimension: 'any', '2d', '3d'
        video_duration: 'any', 'short' (<4min), 'medium' (4-20min), 'long' (>20min)
        video_embeddable: 'any', 'true'
        video_license: 'any', 'creativeCommon', 'youtube'
        video_syndicated: 'any', 'true'
        video_type: 'any', 'episode', 'movie'
        event_type: 'completed', 'live', 'upcoming' (for live streams)
        location: Lat/long coordinates (e.g., '37.42307,-122.08427')
        location_radius: Radius around location (e.g., '50km', '100mi')
        topic_id: Freebase topic ID
        
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
    
    # Date filtering
    if published_after:
        if isinstance(published_after, datetime):
            params["publishedAfter"] = published_after.strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            params["publishedAfter"] = published_after
    if published_before:
        if isinstance(published_before, datetime):
            params["publishedBefore"] = published_before.strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            params["publishedBefore"] = published_before
    
    # Channel filter
    if channel_id:
        params["channelId"] = channel_id
    
    # Region/language
    if region_code:
        params["regionCode"] = region_code
    if relevance_language:
        params["relevanceLanguage"] = relevance_language
    
    # Safe search
    if safe_search:
        params["safeSearch"] = safe_search
    
    # Video filters
    if video_caption:
        params["videoCaption"] = video_caption
    if video_category_id:
        params["videoCategoryId"] = video_category_id
    if video_definition:
        params["videoDefinition"] = video_definition
    if video_dimension:
        params["videoDimension"] = video_dimension
    if video_duration:
        params["videoDuration"] = video_duration
    if video_embeddable:
        params["videoEmbeddable"] = video_embeddable
    if video_license:
        params["videoLicense"] = video_license
    if video_syndicated:
        params["videoSyndicated"] = video_syndicated
    if video_type:
        params["videoType"] = video_type
    
    # Live events
    if event_type:
        params["eventType"] = event_type
    
    # Location-based search
    if location:
        params["location"] = location
        params["locationRadius"] = location_radius or "50km"
    
    # Topic
    if topic_id:
        params["topicId"] = topic_id
    
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
    published_after: Optional[str] = None,
    published_before: Optional[str] = None,
    channel_id: Optional[str] = None,
    region_code: Optional[str] = None,
    relevance_language: Optional[str] = None,
    safe_search: Optional[str] = None,
    video_caption: Optional[str] = None,
    video_category_id: Optional[str] = None,
    video_definition: Optional[str] = None,
    video_dimension: Optional[str] = None,
    video_duration: Optional[str] = None,
    video_embeddable: Optional[str] = None,
    video_license: Optional[str] = None,
    video_syndicated: Optional[str] = None,
    video_type: Optional[str] = None,
    event_type: Optional[str] = None,
    location: Optional[str] = None,
    location_radius: Optional[str] = None,
    topic_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Search for recent videos with full parameter support.
    
    Args:
        query: Search terms
        days_back: How many days back to search (ignored if published_after set)
        max_results: Maximum results
        order: Sort order - date, rating, relevance, title, viewCount
        published_after: ISO 8601 date string (overrides days_back)
        published_before: ISO 8601 date string
        channel_id: Limit to specific channel
        region_code: ISO 3166-1 alpha-2 country code
        relevance_language: ISO 639-1 language code
        safe_search: 'none', 'moderate', 'strict'
        video_caption: 'any', 'closedCaption', 'none'
        video_category_id: YouTube category ID
        video_definition: 'any', 'high', 'standard'
        video_dimension: 'any', '2d', '3d'
        video_duration: 'any', 'short', 'medium', 'long'
        video_embeddable: 'any', 'true'
        video_license: 'any', 'creativeCommon', 'youtube'
        video_syndicated: 'any', 'true'
        video_type: 'any', 'episode', 'movie'
        event_type: 'completed', 'live', 'upcoming'
        location: Lat/long (e.g., '37.42307,-122.08427')
        location_radius: Radius (e.g., '50km')
        topic_id: Freebase topic ID
        
    Returns:
        List of video metadata with full details
    """
    # Calculate published_after from days_back if not explicitly set
    if not published_after:
        after_dt = datetime.utcnow() - timedelta(days=days_back)
        published_after = after_dt
    
    # First get search results
    search_results = search_videos(
        query=query,
        max_results=max_results,
        published_after=published_after,
        published_before=published_before,
        order=order,
        channel_id=channel_id,
        region_code=region_code,
        relevance_language=relevance_language,
        safe_search=safe_search,
        video_caption=video_caption,
        video_category_id=video_category_id,
        video_definition=video_definition,
        video_dimension=video_dimension,
        video_duration=video_duration,
        video_embeddable=video_embeddable,
        video_license=video_license,
        video_syndicated=video_syndicated,
        video_type=video_type,
        event_type=event_type,
        location=location,
        location_radius=location_radius,
        topic_id=topic_id,
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
