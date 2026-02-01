"""Filmot API client - Core API interactions."""

import requests
from typing import Optional, Dict, Any, List, Generator
from .config import BASE_URL, get_headers, validate_config
from .cache import get_cache
from .rate_limiter import get_rate_limiter


class FilmotClient:
    """Client for interacting with the Filmot API."""
    
    def __init__(self, use_cache: bool = True, cache_ttl: int = 3600,
                 rate_limit: float = 2.0, burst_size: int = 5):
        """
        Initialize the Filmot client.
        
        Args:
            use_cache: Enable response caching
            cache_ttl: Cache time-to-live in seconds
            rate_limit: Requests per second limit
            burst_size: Maximum burst of requests
        """
        validate_config()
        self.base_url = BASE_URL
        self.headers = get_headers()
        self.session = requests.Session()
        self.session.headers.update(self.headers)
        
        # Optional caching
        self.use_cache = use_cache
        self.cache = get_cache(ttl=cache_ttl) if use_cache else None
        
        # Rate limiting
        self.rate_limiter = get_rate_limiter(rate_limit, burst_size)
    
    def _request(self, method: str, endpoint: str, params: Optional[Dict] = None, 
                 data: Optional[Dict] = None, skip_cache: bool = False) -> Dict[str, Any]:
        """Make an API request with caching and rate limiting."""
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        
        # Remove None values from params
        if params:
            params = {k: v for k, v in params.items() if v is not None}
        else:
            params = {}
        
        # Check cache first (only for GET requests)
        if method == "GET" and self.use_cache and self.cache and not skip_cache:
            cached = self.cache.get(endpoint, params)
            if cached is not None:
                return cached
        
        # Apply rate limiting
        self.rate_limiter.acquire()
        
        try:
            response = self.session.request(
                method=method,
                url=url,
                params=params,
                json=data
            )
            response.raise_for_status()
            result = response.json()
            
            # Report success to adaptive rate limiter
            if hasattr(self.rate_limiter, 'report_success'):
                self.rate_limiter.report_success()
            
            # Cache successful responses
            if method == "GET" and self.use_cache and self.cache and "error" not in result:
                self.cache.set(endpoint, params, result)
            
            return result
        except requests.exceptions.HTTPError as e:
            # Handle rate limiting
            if response.status_code == 429:
                if hasattr(self.rate_limiter, 'report_rate_limit'):
                    self.rate_limiter.report_rate_limit()
            return {"error": str(e), "status_code": response.status_code}
        except requests.exceptions.RequestException as e:
            return {"error": str(e)}
    
    def get(self, endpoint: str, params: Optional[Dict] = None, skip_cache: bool = False) -> Dict[str, Any]:
        """Make a GET request."""
        return self._request("GET", endpoint, params=params, skip_cache=skip_cache)
    
    # ========== API ENDPOINTS ==========
    
    def search_channels(self, term: str) -> Dict[str, Any]:
        """
        Find YouTube Channels by name or handle.
        
        Args:
            term: Channel name or handle to search for (e.g., "mrbeast")
        
        Returns:
            List of matching channels
        """
        return self.get("/getsearchchannels", params={"term": term})
    
    def get_videos(self, video_ids: str, flags: Optional[int] = None) -> Dict[str, Any]:
        """
        Get basic metadata for a single video or list of videos.
        
        Args:
            video_ids: Single video ID or comma-separated list (e.g., "dQw4w9WgXcQ")
            flags: Optional flags parameter
        
        Returns:
            Video metadata
        """
        params = {"id": video_ids}
        if flags is not None:
            params["flags"] = flags
        return self.get("/getvideos", params=params)
    
    def search_subtitles(
        self,
        query: str,
        lang: Optional[str] = None,
        page: Optional[int] = None,
        category: Optional[str] = None,
        exclude_category: Optional[str] = None,
        license_type: Optional[int] = None,
        min_views: Optional[int] = None,
        max_views: Optional[int] = None,
        min_likes: Optional[int] = None,
        max_likes: Optional[int] = None,
        country: Optional[int] = None,
        channel_id: Optional[str] = None,
        title: Optional[str] = None,
        start_duration: Optional[int] = None,
        end_duration: Optional[int] = None,
        search_manual_subs: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        sort_field: Optional[str] = None,
        sort_order: Optional[str] = None,
        max_query_time: Optional[int] = None,
        hit_format: Optional[int] = None,
        channel: Optional[str] = None,
        channel_count: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Find YouTube Videos by content of subtitles and other parameters.
        
        Args:
            query: Text to find in subtitles. Phrases can be enclosed in double quotes.
            lang: Two-letter language code (e.g., "en", "nl", "fr", "de")
            page: Page number (default 1, max 50 results per page)
            category: Video category (e.g., "Science & Technology", "Gaming")
            exclude_category: Comma-delimited categories to exclude (e.g., "Music,Gaming")
            license_type: 1=Standard YouTube, 2=Creative Commons
            min_views: Minimum view count
            max_views: Maximum view count
            min_likes: Minimum like count
            max_likes: Maximum like count
            country: Country code (integer, e.g., 153=UK, 217=US)
            channel_id: Limit search to specific channel ID(s). Accepts multiple comma-delimited IDs.
            title: Filter by video title
            start_duration: Minimum video duration in seconds
            end_duration: Maximum video duration in seconds
            search_manual_subs: 0=auto subtitles (default), 1=manual subtitles only. Cannot search both.
            start_date: Start date filter (yyyy-mm-dd)
            end_date: End date filter (yyyy-mm-dd)
            sort_field: Sort by field (e.g., "viewcount")
            sort_order: Sort order ("asc" or "desc")
            max_query_time: Max query time in ms (4-15000)
            hit_format: Format of hits array (0 or 1)
            channel: Find top N channels matching text, then search those
            channel_count: Limit top channels when using channel param (default 10)
        
        Returns:
            Search results with matching videos and subtitle hits
        """
        params = {
            "query": query,
            "lang": lang,
            "page": page,
            "category": category,
            "excludeCategory": exclude_category,
            "license": license_type,
            "minViews": min_views,
            "maxViews": max_views,
            "minLikes": min_likes,
            "maxLikes": max_likes,
            "country": country,
            "channelID": channel_id,
            "title": title,
            "startDuration": start_duration,
            "endDuration": end_duration,
            "searchManualSubs": search_manual_subs,
            "startDate": start_date,
            "endDate": end_date,
            "sortField": sort_field,
            "sortOrder": sort_order,
            "maxQueryTime": max_query_time,
            "hitFormat": hit_format,
            "channel": channel,
            "channelCount": channel_count,
        }
        return self.get("/getsearchsubtitles", params=params)
    
    def search_subtitles_paginated(
        self,
        query: str,
        max_pages: int = 10,
        max_results: Optional[int] = None,
        **kwargs
    ) -> Generator[Dict[str, Any], None, None]:
        """
        Search subtitles with automatic pagination.
        
        Yields results page by page until max_pages or max_results is reached.
        
        Args:
            query: Search query
            max_pages: Maximum number of pages to fetch (default 10)
            max_results: Maximum total results to return (optional)
            **kwargs: All other search_subtitles parameters
        
        Yields:
            Each page of results as a dictionary
        """
        total_results = 0
        
        for page_num in range(1, max_pages + 1):
            result = self.search_subtitles(query, page=page_num, **kwargs)
            
            if "error" in result:
                yield result
                break
            
            videos = result.get("result", result.get("videos", []))
            if not videos:
                break  # No more results
            
            yield result
            
            total_results += len(videos)
            
            # Check if we've reached max_results
            if max_results and total_results >= max_results:
                break
            
            # Check if we've fetched all available results
            total_available = result.get("totalresultcount", 0)
            if total_results >= total_available:
                break
    
    def search_subtitles_all(
        self,
        query: str,
        max_pages: int = 10,
        max_results: Optional[int] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Search subtitles and aggregate all pages into a single result.
        
        Args:
            query: Search query
            max_pages: Maximum number of pages to fetch (default 10)
            max_results: Maximum total results to return (optional)
            **kwargs: All other search_subtitles parameters
        
        Returns:
            Aggregated results with all videos combined
        """
        all_videos = []
        total_count = 0
        pages_fetched = 0
        
        for page_result in self.search_subtitles_paginated(query, max_pages, max_results, **kwargs):
            if "error" in page_result:
                if all_videos:
                    # Return what we have so far
                    break
                return page_result
            
            videos = page_result.get("result", page_result.get("videos", []))
            all_videos.extend(videos)
            total_count = page_result.get("totalresultcount", len(all_videos))
            pages_fetched += 1
            
            # Trim to max_results if specified
            if max_results and len(all_videos) >= max_results:
                all_videos = all_videos[:max_results]
                break
        
        return {
            "result": all_videos,
            "totalresultcount": total_count,
            "pages_fetched": pages_fetched,
            "results_returned": len(all_videos)
        }

