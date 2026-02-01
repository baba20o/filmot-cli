"""Watchlist and saved results management for Filmot CLI."""

import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime


class Watchlist:
    """Manage saved videos and search results."""
    
    def __init__(self, storage_dir: str = ".filmot_data"):
        """
        Initialize the watchlist.
        
        Args:
            storage_dir: Directory to store watchlist data
        """
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(exist_ok=True)
        
        self.watchlist_file = self.storage_dir / "watchlist.json"
        self.saved_searches_file = self.storage_dir / "saved_searches.json"
        
        self._watchlist = self._load_file(self.watchlist_file)
        self._saved_searches = self._load_file(self.saved_searches_file)
    
    def _load_file(self, path: Path) -> Dict[str, Any]:
        """Load JSON file or return empty dict."""
        if path.exists():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return {"items": [], "created": datetime.now().isoformat()}
        return {"items": [], "created": datetime.now().isoformat()}
    
    def _save_file(self, path: Path, data: Dict[str, Any]) -> None:
        """Save data to JSON file."""
        data["updated"] = datetime.now().isoformat()
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    
    # ========== WATCHLIST OPERATIONS ==========
    
    def add_video(self, video: Dict[str, Any], notes: str = "") -> bool:
        """
        Add a video to the watchlist.
        
        Args:
            video: Video data from search results
            notes: Optional notes about the video
        
        Returns:
            True if added, False if already exists
        """
        video_id = video.get("id", "")
        
        # Check if already in watchlist
        for item in self._watchlist["items"]:
            if item.get("video_id") == video_id:
                return False
        
        entry = {
            "video_id": video_id,
            "title": video.get("title", ""),
            "channel_name": video.get("channelname", ""),
            "channel_id": video.get("channelid", ""),
            "views": video.get("viewcount", 0),
            "duration": video.get("duration", 0),
            "upload_date": video.get("uploaddate", ""),
            "added_at": datetime.now().isoformat(),
            "notes": notes,
            "tags": [],
            "watched": False
        }
        
        self._watchlist["items"].append(entry)
        self._save_file(self.watchlist_file, self._watchlist)
        return True
    
    def remove_video(self, video_id: str) -> bool:
        """Remove a video from the watchlist."""
        original_len = len(self._watchlist["items"])
        self._watchlist["items"] = [
            item for item in self._watchlist["items"]
            if item.get("video_id") != video_id
        ]
        
        if len(self._watchlist["items"]) < original_len:
            self._save_file(self.watchlist_file, self._watchlist)
            return True
        return False
    
    def mark_watched(self, video_id: str, watched: bool = True) -> bool:
        """Mark a video as watched/unwatched."""
        for item in self._watchlist["items"]:
            if item.get("video_id") == video_id:
                item["watched"] = watched
                item["watched_at"] = datetime.now().isoformat() if watched else None
                self._save_file(self.watchlist_file, self._watchlist)
                return True
        return False
    
    def add_tag(self, video_id: str, tag: str) -> bool:
        """Add a tag to a watchlist video."""
        for item in self._watchlist["items"]:
            if item.get("video_id") == video_id:
                if tag not in item.get("tags", []):
                    item.setdefault("tags", []).append(tag)
                    self._save_file(self.watchlist_file, self._watchlist)
                return True
        return False
    
    def get_watchlist(self, tag: Optional[str] = None, 
                      watched: Optional[bool] = None) -> List[Dict[str, Any]]:
        """
        Get watchlist items with optional filtering.
        
        Args:
            tag: Filter by tag
            watched: Filter by watched status
        
        Returns:
            List of matching watchlist items
        """
        items = self._watchlist["items"]
        
        if tag is not None:
            items = [i for i in items if tag in i.get("tags", [])]
        
        if watched is not None:
            items = [i for i in items if i.get("watched", False) == watched]
        
        return items
    
    def clear_watchlist(self) -> int:
        """Clear all watchlist items. Returns count of items removed."""
        count = len(self._watchlist["items"])
        self._watchlist["items"] = []
        self._save_file(self.watchlist_file, self._watchlist)
        return count
    
    # ========== SAVED SEARCHES ==========
    
    def save_search(self, name: str, query: str, params: Dict[str, Any],
                    results: Optional[Dict[str, Any]] = None) -> bool:
        """
        Save a search for later reference.
        
        Args:
            name: Name for this saved search
            query: Search query
            params: Search parameters
            results: Optional results to cache
        
        Returns:
            True if saved successfully
        """
        entry = {
            "name": name,
            "query": query,
            "params": params,
            "saved_at": datetime.now().isoformat(),
            "result_count": len(results.get("result", [])) if results else 0,
            "results": results
        }
        
        # Update existing or add new
        found = False
        for i, item in enumerate(self._saved_searches["items"]):
            if item.get("name") == name:
                self._saved_searches["items"][i] = entry
                found = True
                break
        
        if not found:
            self._saved_searches["items"].append(entry)
        
        self._save_file(self.saved_searches_file, self._saved_searches)
        return True
    
    def get_saved_search(self, name: str) -> Optional[Dict[str, Any]]:
        """Get a saved search by name."""
        for item in self._saved_searches["items"]:
            if item.get("name") == name:
                return item
        return None
    
    def list_saved_searches(self) -> List[Dict[str, Any]]:
        """List all saved searches (without full results)."""
        return [
            {
                "name": item.get("name"),
                "query": item.get("query"),
                "params": item.get("params"),
                "saved_at": item.get("saved_at"),
                "result_count": item.get("result_count", 0)
            }
            for item in self._saved_searches["items"]
        ]
    
    def delete_saved_search(self, name: str) -> bool:
        """Delete a saved search."""
        original_len = len(self._saved_searches["items"])
        self._saved_searches["items"] = [
            item for item in self._saved_searches["items"]
            if item.get("name") != name
        ]
        
        if len(self._saved_searches["items"]) < original_len:
            self._save_file(self.saved_searches_file, self._saved_searches)
            return True
        return False
    
    def stats(self) -> Dict[str, Any]:
        """Get watchlist statistics."""
        items = self._watchlist["items"]
        watched_count = sum(1 for i in items if i.get("watched", False))
        
        all_tags = []
        for item in items:
            all_tags.extend(item.get("tags", []))
        unique_tags = list(set(all_tags))
        
        return {
            "total_videos": len(items),
            "watched": watched_count,
            "unwatched": len(items) - watched_count,
            "tags": unique_tags,
            "saved_searches": len(self._saved_searches["items"]),
            "storage_dir": str(self.storage_dir)
        }


# Global instance
_watchlist: Optional[Watchlist] = None


def get_watchlist() -> Watchlist:
    """Get or create the global watchlist instance."""
    global _watchlist
    if _watchlist is None:
        _watchlist = Watchlist()
    return _watchlist
