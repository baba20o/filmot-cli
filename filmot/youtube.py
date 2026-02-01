"""YouTube Data API integration for searching recent videos."""

import os
from datetime import datetime, timedelta
from typing import Optional
from dotenv import load_dotenv

load_dotenv()


def get_youtube_api_key() -> str:
    """Get YouTube API key from environment."""
    key = os.getenv("YOUTUBE_API_KEY", "")
    if not key:
        raise ValueError("YOUTUBE_API_KEY not found in .env file")
    return key


def search_youtube_videos(
    query: str,
    days: int = None,
    published_after: str = None,
    published_before: str = None,
    max_results: int = 25,
    order: str = "date",
    channel_id: str = None,
    region_code: str = None,
    relevance_language: str = None,
    safe_search: str = None,
    video_caption: str = None,
    video_category_id: str = None,
    video_definition: str = None,
    video_dimension: str = None,
    video_duration: str = None,
    video_embeddable: str = None,
    video_license: str = None,
    video_syndicated: str = None,
    video_type: str = None,
    event_type: str = None,
    location: str = None,
    location_radius: str = None,
    topic_id: str = None,
) -> list[dict]:
    """
    Search YouTube for videos matching query with full API parameter support.
    
    Args:
        query: Search query (searches title, description, tags)
        days: How many days back to search (alternative to published_after)
        published_after: ISO 8601 date (e.g., 2026-01-25T00:00:00Z)
        published_before: ISO 8601 date
        max_results: Maximum results to return (default 25, max 50)
        order: Sort order - 'date', 'viewCount', 'relevance', 'rating', 'title'
        channel_id: Filter by channel ID
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
        List of video dicts with title, channel, views, url, etc.
    """
    try:
        from googleapiclient.discovery import build
    except ImportError:
        raise ImportError(
            "google-api-python-client not installed. Run: pip install google-api-python-client"
        )
    
    api_key = get_youtube_api_key()
    youtube = build('youtube', 'v3', developerKey=api_key)
    
    # Build search parameters
    search_params = {
        'q': query,
        'part': 'id,snippet',
        'type': 'video',
        'maxResults': min(max_results, 50),
        'order': order,
    }
    
    # Date filtering
    if published_after:
        search_params['publishedAfter'] = published_after
    elif days:
        after_date = (datetime.utcnow() - timedelta(days=days)).isoformat() + 'Z'
        search_params['publishedAfter'] = after_date
    
    if published_before:
        search_params['publishedBefore'] = published_before
    
    # Channel filter
    if channel_id:
        search_params['channelId'] = channel_id
    
    # Region/language
    if region_code:
        search_params['regionCode'] = region_code
    if relevance_language:
        search_params['relevanceLanguage'] = relevance_language
    
    # Safe search
    if safe_search:
        search_params['safeSearch'] = safe_search
    
    # Video filters
    if video_caption:
        search_params['videoCaption'] = video_caption
    if video_category_id:
        search_params['videoCategoryId'] = video_category_id
    if video_definition:
        search_params['videoDefinition'] = video_definition
    if video_dimension:
        search_params['videoDimension'] = video_dimension
    if video_duration:
        search_params['videoDuration'] = video_duration
    if video_embeddable:
        search_params['videoEmbeddable'] = video_embeddable
    if video_license:
        search_params['videoLicense'] = video_license
    if video_syndicated:
        search_params['videoSyndicated'] = video_syndicated
    if video_type:
        search_params['videoType'] = video_type
    
    # Live events
    if event_type:
        search_params['eventType'] = event_type
    
    # Location-based search
    if location:
        search_params['location'] = location
        if location_radius:
            search_params['locationRadius'] = location_radius
        else:
            search_params['locationRadius'] = '50km'  # default radius
    
    # Topic
    if topic_id:
        search_params['topicId'] = topic_id
    
    # Execute search
    search_response = youtube.search().list(**search_params).execute()
    
    video_ids = [item['id']['videoId'] for item in search_response.get('items', [])]
    
    if not video_ids:
        return []
    
    # Get video statistics
    videos_response = youtube.videos().list(
        part='snippet,statistics,contentDetails',
        id=','.join(video_ids)
    ).execute()
    
    results = []
    for video in videos_response.get('items', []):
        snippet = video['snippet']
        stats = video.get('statistics', {})
        content = video.get('contentDetails', {})
        
        results.append({
            'video_id': video['id'],
            'title': snippet['title'],
            'channel_title': snippet['channelTitle'],
            'channel_id': snippet['channelId'],
            'description': snippet.get('description', '')[:500],
            'published_at': snippet['publishedAt'],
            'views': int(stats.get('viewCount', 0)),
            'likes': int(stats.get('likeCount', 0)),
            'comments': int(stats.get('commentCount', 0)),
            'duration': content.get('duration', 'PT0S'),
            'definition': content.get('definition', 'sd'),
            'caption': content.get('caption', 'false'),
            'licensed_content': content.get('licensedContent', False),
            'category_id': snippet.get('categoryId', ''),
            'tags': snippet.get('tags', []),
            'url': f"https://youtube.com/watch?v={video['id']}",
        })
    
    return results


def search_with_transcript(
    query: str,
    transcript_query: Optional[str] = None,
    days: int = 7,
    max_results: int = 10,
) -> list[dict]:
    """
    Search YouTube and fetch transcripts for matching videos.
    
    Args:
        query: Search query for YouTube
        transcript_query: Optional different query for transcript search
        days: How many days back to search
        max_results: Max videos to process
    
    Returns:
        List of videos with transcript matches
    """
    from .transcript import search_in_transcript
    
    videos = search_youtube_videos(query, days=days, max_results=max_results)
    
    search_term = transcript_query or query
    results_with_transcripts = []
    
    for video in videos:
        try:
            transcript_result = search_in_transcript(video['video_id'], search_term)
            video['transcript_matches'] = transcript_result.get('matches', [])
            video['transcript_match_count'] = transcript_result.get('match_count', 0)
        except Exception:
            video['transcript_matches'] = []
            video['transcript_match_count'] = 0
        
        results_with_transcripts.append(video)
    
    return results_with_transcripts
