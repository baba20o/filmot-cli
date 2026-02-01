"""Rate limiting for Filmot API requests."""

import time
import threading
from typing import Optional
from collections import deque


class RateLimiter:
    """Token bucket rate limiter with sliding window."""
    
    def __init__(self, requests_per_second: float = 1.0, burst_size: int = 5):
        """
        Initialize the rate limiter.
        
        Args:
            requests_per_second: Maximum sustained request rate
            burst_size: Maximum burst of requests allowed
        """
        self.requests_per_second = requests_per_second
        self.burst_size = burst_size
        self.min_interval = 1.0 / requests_per_second
        
        # Sliding window of request timestamps
        self.request_times: deque = deque(maxlen=burst_size)
        self.lock = threading.Lock()
        
        # Stats
        self.total_requests = 0
        self.total_waits = 0
        self.total_wait_time = 0.0
    
    def acquire(self) -> float:
        """
        Acquire permission to make a request, blocking if necessary.
        
        Returns:
            Time waited in seconds (0 if no wait was needed)
        """
        with self.lock:
            current_time = time.time()
            wait_time = 0.0
            
            # Clean old requests outside our window
            window_start = current_time - 1.0  # 1 second window
            while self.request_times and self.request_times[0] < window_start:
                self.request_times.popleft()
            
            # Check if we're at burst capacity
            if len(self.request_times) >= self.burst_size:
                # Need to wait until oldest request exits the window
                oldest = self.request_times[0]
                wait_time = (oldest + 1.0) - current_time
                
                if wait_time > 0:
                    self.total_waits += 1
                    self.total_wait_time += wait_time
            
            # Also ensure minimum interval between requests
            if self.request_times:
                last_request = self.request_times[-1]
                interval_wait = (last_request + self.min_interval) - current_time
                if interval_wait > wait_time:
                    wait_time = interval_wait
        
        # Wait outside the lock
        if wait_time > 0:
            time.sleep(wait_time)
        
        # Record this request
        with self.lock:
            self.request_times.append(time.time())
            self.total_requests += 1
        
        return wait_time
    
    def stats(self) -> dict:
        """Get rate limiter statistics."""
        with self.lock:
            return {
                "total_requests": self.total_requests,
                "total_waits": self.total_waits,
                "total_wait_time": round(self.total_wait_time, 2),
                "avg_wait_time": round(self.total_wait_time / max(self.total_waits, 1), 3),
                "requests_per_second": self.requests_per_second,
                "burst_size": self.burst_size
            }
    
    def reset_stats(self):
        """Reset statistics."""
        with self.lock:
            self.total_requests = 0
            self.total_waits = 0
            self.total_wait_time = 0.0
    
    def report_success(self):
        """Report a successful request (no-op in base class)."""
        pass
    
    def report_rate_limit(self):
        """Report a rate limit error (no-op in base class)."""
        pass


class AdaptiveRateLimiter(RateLimiter):
    """Rate limiter that adapts based on API response codes."""
    
    def __init__(self, requests_per_second: float = 1.0, burst_size: int = 5):
        super().__init__(requests_per_second, burst_size)
        self.consecutive_errors = 0
        self.backoff_factor = 1.0
        self.max_backoff = 10.0
    
    def report_success(self):
        """Report a successful request."""
        with self.lock:
            self.consecutive_errors = 0
            # Gradually recover from backoff
            self.backoff_factor = max(1.0, self.backoff_factor * 0.9)
    
    def report_rate_limit(self):
        """Report a rate limit error (429)."""
        with self.lock:
            self.consecutive_errors += 1
            # Exponential backoff
            self.backoff_factor = min(self.max_backoff, self.backoff_factor * 2)
    
    def acquire(self) -> float:
        """Acquire with adaptive backoff."""
        # Apply backoff factor to wait time
        base_wait = super().acquire()
        
        with self.lock:
            if self.backoff_factor > 1.0:
                extra_wait = (self.backoff_factor - 1.0) * self.min_interval
                if extra_wait > 0:
                    time.sleep(extra_wait)
                    return base_wait + extra_wait
        
        return base_wait


# Global rate limiter instance
_rate_limiter: Optional[RateLimiter] = None


def get_rate_limiter(requests_per_second: float = 2.0, burst_size: int = 5) -> RateLimiter:
    """Get or create the global rate limiter instance."""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = AdaptiveRateLimiter(requests_per_second, burst_size)
    return _rate_limiter
