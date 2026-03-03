"""
Channel Transcript Downloader - Download all transcripts from a YouTube channel.

Downloads every transcript from a channel and stores them locally with a manifest
for resumable downloads. Designed to build local knowledge corpora that AI agents
can mine for insights.

Storage structure:
    .filmot_data/
        channels/
            chat-with-traders/
                manifest.json          # Download state, channel metadata, video index
                transcripts/
                    Co_GYku903k.json   # Individual transcript files
                    HSuE19H1-K0.json
                    ...
"""

import json
import os
import re
import time
from pathlib import Path
from datetime import datetime
from typing import Optional, Callable

from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# Local query / proximity search helpers
# ---------------------------------------------------------------------------

def _tokenize_words(text: str) -> list[tuple[str, int]]:
    """Return list of (lowercase_word, char_offset) for every word in *text*."""
    return [(m.group().lower(), m.start()) for m in re.finditer(r"\S+", text)]


def _parse_proximity_query(query: str):
    """Parse a query string looking for proximity operators.

    Supported syntaxes (case-insensitive):
        "phrase1" NEAR/N "phrase2"   – two phrases within N words
        "word1 word2"~N             – words in the phrase within N words of each other

    Returns one of:
        ('plain', query_str)
        ('near', phrase1, phrase2, distance)
        ('tilde', words_list, distance)
    """
    # --- NEAR/N between two quoted phrases ---
    m = re.match(
        r'''^\s*"([^"]+)"\s+NEAR\s*/\s*(\d+)\s+"([^"]+)"\s*$''',
        query, re.IGNORECASE,
    )
    if m:
        return ('near', m.group(1).strip(), m.group(3).strip(), int(m.group(2)))

    # --- "words"~N  (tilde proximity) ---
    m = re.match(r'''^\s*"([^"]+)"~(\d+)\s*$''', query)
    if m:
        words = m.group(1).strip().split()
        if len(words) >= 2:
            return ('tilde', words, int(m.group(2)))

    return ('plain', query)


def _find_near_matches(text: str, phrase1: str, phrase2: str, distance: int):
    """Find positions where *phrase1* and *phrase2* appear within *distance* words.

    Returns list of (start_char, end_char) spans covering both matches.
    """
    text_lower = text.lower()
    p1 = phrase1.lower()
    p2 = phrase2.lower()

    # Collect all occurrence positions for each phrase
    def _all_positions(hay: str, needle: str):
        out = []
        pos = 0
        while True:
            idx = hay.find(needle, pos)
            if idx == -1:
                break
            out.append(idx)
            pos = idx + 1
        return out

    p1_positions = _all_positions(text_lower, p1)
    p2_positions = _all_positions(text_lower, p2)

    if not p1_positions or not p2_positions:
        return []

    # For word-distance we need a word index.  Build char→word_index map.
    tokens = _tokenize_words(text)
    # Map: char_offset → word_index (for the first char of each token)
    char_to_widx = {}
    for widx, (_, coff) in enumerate(tokens):
        char_to_widx[coff] = widx

    def _word_index_of(char_pos: int) -> int | None:
        """Find the word index for a character position (snap to nearest token start)."""
        best = None
        best_dist = float('inf')
        for coff, widx in char_to_widx.items():
            d = abs(coff - char_pos)
            if d < best_dist:
                best_dist = d
                best = widx
        return best

    # Pre-compute word indices for all positions (O(n) approach)
    # Build sorted list of token starts for binary-search snapping
    token_starts = [coff for (_, coff) in tokens]

    def _snap_word_index(char_pos: int) -> int:
        """Binary search for nearest token start."""
        import bisect
        i = bisect.bisect_left(token_starts, char_pos)
        if i == 0:
            return 0
        if i >= len(token_starts):
            return len(token_starts) - 1
        before = token_starts[i - 1]
        after = token_starts[i]
        if char_pos - before <= after - char_pos:
            return i - 1
        return i

    matches = []
    for cp1 in p1_positions:
        wi1 = _snap_word_index(cp1)
        for cp2 in p2_positions:
            wi2 = _snap_word_index(cp2)
            if abs(wi1 - wi2) <= distance:
                span_start = min(cp1, cp2)
                span_end = max(cp1 + len(p1), cp2 + len(p2))
                matches.append((span_start, span_end))

    # Deduplicate overlapping spans
    if not matches:
        return []
    matches.sort()
    merged = [matches[0]]
    for s, e in matches[1:]:
        if s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def _find_tilde_matches(text: str, words: list[str], distance: int):
    """Find positions where all *words* appear within *distance* words of each other.

    Returns list of (start_char, end_char) spans.
    """
    text_lower = text.lower()
    words_lower = [w.lower() for w in words]

    # Collect all positions per word
    def _all_positions(hay: str, needle: str):
        out = []
        pos = 0
        while True:
            idx = hay.find(needle, pos)
            if idx == -1:
                break
            out.append(idx)
            pos = idx + 1
        return out

    word_positions = [_all_positions(text_lower, w) for w in words_lower]
    if any(len(p) == 0 for p in word_positions):
        return []

    tokens = _tokenize_words(text)
    token_starts = [coff for (_, coff) in tokens]

    def _snap_word_index(char_pos: int) -> int:
        import bisect
        i = bisect.bisect_left(token_starts, char_pos)
        if i == 0:
            return 0
        if i >= len(token_starts):
            return len(token_starts) - 1
        before = token_starts[i - 1]
        after = token_starts[i]
        if char_pos - before <= after - char_pos:
            return i - 1
        return i

    # For 2 words, simple pair check. For N words, check all combos of positions.
    # Since most queries will be 2-3 words, brute-force is fine.
    from itertools import product

    matches = []
    for combo in product(*word_positions):
        word_indices = [_snap_word_index(cp) for cp in combo]
        span = max(word_indices) - min(word_indices)
        if span <= distance:
            span_start = min(combo)
            span_end = max(cp + len(w) for cp, w in zip(combo, words_lower))
            matches.append((span_start, span_end))

    if not matches:
        return []
    matches.sort()
    merged = [matches[0]]
    for s, e in matches[1:]:
        if s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def _slugify(name: str) -> str:
    """Convert channel name to filesystem-safe slug."""
    slug = name.lower().strip()
    slug = re.sub(r'[\s_]+', '-', slug)
    slug = re.sub(r'[^a-z0-9\-]', '', slug)
    slug = re.sub(r'-+', '-', slug)
    return slug.strip('-') or 'unknown-channel'


def get_channel_info(channel_id: str) -> dict:
    """
    Get channel metadata from YouTube Data API.
    
    Returns:
        dict with channel_id, name, uploads_playlist_id, subscriber_count, video_count, description
    """
    from googleapiclient.discovery import build
    
    api_key = os.getenv("YOUTUBE_API_KEY", "")
    if not api_key:
        raise ValueError("YOUTUBE_API_KEY not found in .env")
    
    yt = build('youtube', 'v3', developerKey=api_key)
    
    response = yt.channels().list(
        part='snippet,contentDetails,statistics',
        id=channel_id
    ).execute()
    
    if not response.get('items'):
        raise ValueError(f"Channel not found: {channel_id}")
    
    ch = response['items'][0]
    return {
        'channel_id': channel_id,
        'name': ch['snippet']['title'],
        'description': ch['snippet'].get('description', ''),
        'uploads_playlist_id': ch['contentDetails']['relatedPlaylists']['uploads'],
        'subscriber_count': int(ch['statistics'].get('subscriberCount', 0)),
        'video_count': int(ch['statistics'].get('videoCount', 0)),
    }


def list_all_video_ids(uploads_playlist_id: str, progress_callback: Optional[Callable] = None) -> list[dict]:
    """
    Enumerate ALL videos in a channel's uploads playlist.
    
    Returns:
        list of dicts with video_id, title, published_at, description
    """
    from googleapiclient.discovery import build
    
    api_key = os.getenv("YOUTUBE_API_KEY", "")
    if not api_key:
        raise ValueError("YOUTUBE_API_KEY not found in .env")
    
    yt = build('youtube', 'v3', developerKey=api_key)
    
    videos = []
    next_page_token = None
    page = 0
    
    while True:
        page += 1
        request = yt.playlistItems().list(
            part='snippet',
            playlistId=uploads_playlist_id,
            maxResults=50,  # Maximum allowed
            pageToken=next_page_token,
        )
        response = request.execute()
        
        for item in response.get('items', []):
            snippet = item['snippet']
            vid = snippet['resourceId']['videoId']
            videos.append({
                'video_id': vid,
                'title': snippet.get('title', ''),
                'published_at': snippet.get('publishedAt', ''),
                'description': snippet.get('description', '')[:500],  # Truncate long descriptions
            })
        
        if progress_callback:
            total = response['pageInfo']['totalResults']
            progress_callback(len(videos), total, page)
        
        next_page_token = response.get('nextPageToken')
        if not next_page_token:
            break
    
    return videos


class ChannelDownloader:
    """Manages downloading and storing all transcripts for a YouTube channel."""
    
    def __init__(self, data_dir: str = ".filmot_data"):
        self.data_dir = Path(data_dir)
        self.channels_dir = self.data_dir / "channels"
        self.channels_dir.mkdir(parents=True, exist_ok=True)
    
    def _get_channel_dir(self, slug: str) -> Path:
        """Get or create directory for a channel."""
        channel_dir = self.channels_dir / slug
        channel_dir.mkdir(parents=True, exist_ok=True)
        (channel_dir / "transcripts").mkdir(exist_ok=True)
        return channel_dir
    
    def _load_manifest(self, channel_dir: Path) -> dict:
        """Load existing manifest or return empty structure."""
        manifest_path = channel_dir / "manifest.json"
        if manifest_path.exists():
            with open(manifest_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}
    
    def _save_manifest(self, channel_dir: Path, manifest: dict):
        """Save manifest atomically."""
        manifest_path = channel_dir / "manifest.json"
        tmp_path = channel_dir / "manifest.json.tmp"
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
        tmp_path.replace(manifest_path)
    
    def _save_transcript(self, channel_dir: Path, video_id: str, data: dict):
        """Save individual transcript file."""
        safe_id = re.sub(r'[/\\:*?"<>|]', '_', video_id)
        path = channel_dir / "transcripts" / f"{safe_id}.json"
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return path
    
    def get_downloaded_channels(self) -> list[dict]:
        """List all channels that have been downloaded."""
        channels = []
        if not self.channels_dir.exists():
            return channels
        for d in sorted(self.channels_dir.iterdir()):
            if d.is_dir():
                manifest = self._load_manifest(d)
                if manifest:
                    channels.append({
                        'slug': d.name,
                        'name': manifest.get('channel', {}).get('name', d.name),
                        'channel_id': manifest.get('channel', {}).get('channel_id', ''),
                        'total_videos': manifest.get('total_videos', 0),
                        'downloaded': manifest.get('downloaded_count', 0),
                        'failed': manifest.get('failed_count', 0),
                        'last_updated': manifest.get('last_updated', ''),
                    })
        return channels

    def download_channel(
        self,
        channel_id: str,
        delay: float = 1.0,
        lang: Optional[list[str]] = None,
        progress_callback: Optional[Callable] = None,
        log_callback: Optional[Callable] = None,
    ) -> dict:
        """
        Download all transcripts from a channel with resume support.
        
        Args:
            channel_id: YouTube channel ID
            delay: Seconds between transcript downloads (rate limiting)
            lang: Preferred languages (default: ['en'])
            progress_callback: Called with (current, total, video_id, status)
            log_callback: Called with (level, message) for logging
            
        Returns:
            dict with summary statistics
        """
        from .transcript import get_transcript
        
        if lang is None:
            lang = ['en', 'en-US', 'en-GB']
        
        def log(level: str, msg: str):
            if log_callback:
                log_callback(level, msg)
        
        # STEP 1: Get channel info
        log('info', f'Fetching channel info for {channel_id}...')
        channel_info = get_channel_info(channel_id)
        slug = _slugify(channel_info['name'])
        channel_dir = self._get_channel_dir(slug)
        
        log('info', f"Channel: {channel_info['name']} ({channel_info['video_count']} videos)")
        log('info', f"Storage: {channel_dir}")
        
        # STEP 2: Load existing manifest (for resume)
        manifest = self._load_manifest(channel_dir)
        existing_videos = manifest.get('videos', {})
        
        # STEP 3: Enumerate all video IDs
        log('info', 'Enumerating all videos...')
        
        def enum_progress(current, total, page):
            log('info', f"  Listed {current}/{total} videos (page {page})")
        
        all_videos = list_all_video_ids(
            channel_info['uploads_playlist_id'],
            progress_callback=enum_progress
        )
        log('info', f"Found {len(all_videos)} videos total")
        
        # STEP 4: Determine delta (what's new/not yet downloaded)
        already_done = set()
        already_failed = set()
        for vid_id, vid_info in existing_videos.items():
            status = vid_info.get('status', '')
            if status == 'done':
                already_done.add(vid_id)
            elif status == 'failed':
                already_failed.add(vid_id)
        
        # Build download queue: new videos + previously failed (retry)
        to_download = []
        for v in all_videos:
            vid_id = v['video_id']
            if vid_id in already_done:
                continue
            to_download.append(v)
        
        new_count = len([v for v in to_download if v['video_id'] not in already_failed])
        retry_count = len([v for v in to_download if v['video_id'] in already_failed])
        
        log('info', f"Already downloaded: {len(already_done)}")
        if retry_count > 0:
            log('info', f"Retrying failed: {retry_count}")
        log('info', f"New to download: {new_count}")
        log('info', f"Total to process: {len(to_download)}")
        
        if not to_download:
            log('info', "Nothing new to download — channel is fully synced!")
            # Still update manifest with latest video list
            manifest.update({
                'channel': channel_info,
                'total_videos': len(all_videos),
                'downloaded_count': len(already_done),
                'failed_count': len(already_failed),
                'last_updated': datetime.now().isoformat(),
                'last_sync': datetime.now().isoformat(),
            })
            self._save_manifest(channel_dir, manifest)
            return {
                'channel': channel_info['name'],
                'slug': slug,
                'total': len(all_videos),
                'downloaded': len(already_done),
                'new': 0,
                'failed': 0,
                'skipped': 0,
            }
        
        # STEP 5: Download transcripts
        session_downloaded = 0
        session_failed = 0
        session_skipped = 0
        
        # Ensure videos dict exists in manifest
        if 'videos' not in manifest:
            manifest['videos'] = {}
        
        # Add all known videos to manifest (even if not downloaded yet)
        for v in all_videos:
            vid_id = v['video_id']
            if vid_id not in manifest['videos']:
                manifest['videos'][vid_id] = {
                    'title': v['title'],
                    'published_at': v['published_at'],
                    'status': 'pending',
                }
        
        for i, v in enumerate(to_download):
            vid_id = v['video_id']
            title = v['title']
            
            if progress_callback:
                progress_callback(i + 1, len(to_download), vid_id, 'downloading')
            
            log('debug', f"[{i+1}/{len(to_download)}] {title[:60]}...")
            
            try:
                result = get_transcript(vid_id, languages=lang)
                
                if 'error' in result:
                    log('warn', f"  ✗ {result['error']}")
                    manifest['videos'][vid_id].update({
                        'status': 'failed',
                        'error': result['error'],
                        'last_attempt': datetime.now().isoformat(),
                    })
                    session_failed += 1
                else:
                    # Save transcript file
                    transcript_data = {
                        'video_id': vid_id,
                        'title': title,
                        'published_at': v['published_at'],
                        'channel': channel_info['name'],
                        'channel_id': channel_id,
                        'language': result.get('language', ''),
                        'is_generated': result.get('is_generated', True),
                        'duration_seconds': result.get('duration_seconds', 0),
                        'segment_count': result.get('segment_count', 0),
                        'word_count': len(result.get('full_text', '').split()),
                        'full_text': result.get('full_text', ''),
                        'segments': result.get('segments', []),
                        'downloaded_at': datetime.now().isoformat(),
                    }
                    self._save_transcript(channel_dir, vid_id, transcript_data)
                    
                    manifest['videos'][vid_id].update({
                        'status': 'done',
                        'language': result.get('language', ''),
                        'is_generated': result.get('is_generated', True),
                        'duration_seconds': result.get('duration_seconds', 0),
                        'word_count': len(result.get('full_text', '').split()),
                        'downloaded_at': datetime.now().isoformat(),
                    })
                    session_downloaded += 1
                    log('debug', f"  ✓ {result.get('segment_count', 0)} segments, {len(result.get('full_text', '').split())} words")
                    
            except Exception as e:
                log('warn', f"  ✗ Exception: {e}")
                manifest['videos'][vid_id].update({
                    'status': 'failed',
                    'error': str(e),
                    'last_attempt': datetime.now().isoformat(),
                })
                session_failed += 1
            
            # Save manifest after EVERY video (crash-safe resume)
            done_total = sum(1 for v in manifest['videos'].values() if v.get('status') == 'done')
            failed_total = sum(1 for v in manifest['videos'].values() if v.get('status') == 'failed')
            
            manifest.update({
                'channel': channel_info,
                'total_videos': len(all_videos),
                'downloaded_count': done_total,
                'failed_count': failed_total,
                'last_updated': datetime.now().isoformat(),
            })
            self._save_manifest(channel_dir, manifest)
            
            if progress_callback:
                progress_callback(i + 1, len(to_download), vid_id, 'done')
            
            # Rate limiting
            if i < len(to_download) - 1:
                time.sleep(delay)
        
        # Final manifest update
        manifest['last_sync'] = datetime.now().isoformat()
        self._save_manifest(channel_dir, manifest)
        
        summary = {
            'channel': channel_info['name'],
            'slug': slug,
            'total': len(all_videos),
            'already_had': len(already_done),
            'downloaded': session_downloaded,
            'failed': session_failed,
            'skipped': session_skipped,
            'storage': str(channel_dir),
        }
        
        log('info', f"\nDone! Downloaded {session_downloaded}, failed {session_failed}")
        log('info', f"Total corpus: {len(already_done) + session_downloaded}/{len(all_videos)} videos")
        
        return summary

    def get_channel_stats(self, slug: str) -> Optional[dict]:
        """Get statistics for a downloaded channel."""
        channel_dir = self.channels_dir / slug
        manifest = self._load_manifest(channel_dir)
        if not manifest:
            return None
        
        videos = manifest.get('videos', {})
        done_videos = {k: v for k, v in videos.items() if v.get('status') == 'done'}
        failed_videos = {k: v for k, v in videos.items() if v.get('status') == 'failed'}
        pending_videos = {k: v for k, v in videos.items() if v.get('status') == 'pending'}
        
        total_words = sum(v.get('word_count', 0) for v in done_videos.values())
        total_duration = sum(v.get('duration_seconds', 0) for v in done_videos.values())
        
        return {
            'channel': manifest.get('channel', {}),
            'slug': slug,
            'total_videos': manifest.get('total_videos', 0),
            'downloaded': len(done_videos),
            'failed': len(failed_videos),
            'pending': len(pending_videos),
            'total_words': total_words,
            'total_duration_hours': round(total_duration / 3600, 1),
            'last_updated': manifest.get('last_updated', ''),
            'last_sync': manifest.get('last_sync', ''),
            'storage_path': str(channel_dir),
        }
    
    def search_corpus(self, slug: str, query: str, case_sensitive: bool = False) -> list[dict]:
        """
        Search across all downloaded transcripts for a channel.

        Supports:
          - Plain substring search:  "machine learning"
          - NEAR proximity:          "machine learning" NEAR/10 "neural network"
          - Tilde proximity:         "deep learning tensorflow"~5

        Returns list of matches with video_id, title, context snippets.
        """
        channel_dir = self.channels_dir / slug
        transcripts_dir = channel_dir / "transcripts"

        if not transcripts_dir.exists():
            return []

        parsed = _parse_proximity_query(query)
        results = []

        for f in sorted(transcripts_dir.glob("*.json")):
            with open(f, 'r', encoding='utf-8') as fh:
                data = json.load(fh)

            text = data.get('full_text', '')
            if not text:
                continue

            # --- Proximity search paths ---
            if parsed[0] == 'near':
                _, phrase1, phrase2, dist = parsed
                spans = _find_near_matches(text, phrase1, phrase2, dist)
                if not spans:
                    continue
                snippets = self._snippets_from_spans(text, spans)
                results.append({
                    'video_id': data.get('video_id', f.stem),
                    'title': data.get('title', ''),
                    'published_at': data.get('published_at', ''),
                    'match_count': len(spans),
                    'snippets': snippets,
                })

            elif parsed[0] == 'tilde':
                _, words, dist = parsed
                spans = _find_tilde_matches(text, words, dist)
                if not spans:
                    continue
                snippets = self._snippets_from_spans(text, spans)
                results.append({
                    'video_id': data.get('video_id', f.stem),
                    'title': data.get('title', ''),
                    'published_at': data.get('published_at', ''),
                    'match_count': len(spans),
                    'snippets': snippets,
                })

            else:
                # --- Plain substring search ---
                _, raw_query = parsed
                query_lower = raw_query if case_sensitive else raw_query.lower()
                search_text = text if case_sensitive else text.lower()

                if query_lower not in search_text:
                    continue

                snippets = []
                pos = 0
                while True:
                    idx = search_text.find(query_lower, pos)
                    if idx == -1:
                        break
                    start = max(0, idx - 200)
                    end = min(len(text), idx + len(query) + 200)
                    snippet = text[start:end].strip()
                    if start > 0:
                        snippet = '...' + snippet
                    if end < len(text):
                        snippet = snippet + '...'
                    snippets.append(snippet)
                    pos = idx + 1
                    if len(snippets) >= 5:
                        break

                results.append({
                    'video_id': data.get('video_id', f.stem),
                    'title': data.get('title', ''),
                    'published_at': data.get('published_at', ''),
                    'match_count': search_text.count(query_lower),
                    'snippets': snippets,
                })

        results.sort(key=lambda x: x['match_count'], reverse=True)
        return results

    @staticmethod
    def _snippets_from_spans(text: str, spans: list[tuple[int, int]], max_snippets: int = 5) -> list[str]:
        """Extract context-window snippets from character spans."""
        snippets = []
        for span_start, span_end in spans[:max_snippets]:
            ctx_start = max(0, span_start - 200)
            ctx_end = min(len(text), span_end + 200)
            snippet = text[ctx_start:ctx_end].strip()
            if ctx_start > 0:
                snippet = '...' + snippet
            if ctx_end < len(text):
                snippet = snippet + '...'
            snippets.append(snippet)
        return snippets
