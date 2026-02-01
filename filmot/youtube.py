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
    days: int = 7,
    max_results: int = 25,
    order: str = "date"
) -> list[dict]:
    """
    Search YouTube for recent videos matching query.
    
    Args:
        query: Search query (searches title, description, tags)
        days: How many days back to search (default 7)
        max_results: Maximum results to return (default 25, max 50)
        order: Sort order - 'date', 'viewCount', 'relevance', 'rating'
    
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
    
    # Calculate date range
    after_date = (datetime.utcnow() - timedelta(days=days)).isoformat() + 'Z'
    
    # Search for videos
    search_response = youtube.search().list(
        q=query,
        part='id,snippet',
        type='video',
        maxResults=min(max_results, 50),
        order=order,
        publishedAfter=after_date,
    ).execute()
    
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
            'description': snippet.get('description', '')[:200],
            'published_at': snippet['publishedAt'],
            'views': int(stats.get('viewCount', 0)),
            'likes': int(stats.get('likeCount', 0)),
            'comments': int(stats.get('commentCount', 0)),
            'duration': content.get('duration', 'PT0S'),
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
