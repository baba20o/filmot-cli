"""Batch operations for processing multiple queries."""

import json
import csv
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass
from datetime import datetime


@dataclass
class BatchQuery:
    """Represents a single query in a batch."""
    query: str
    params: Dict[str, Any]
    name: Optional[str] = None


@dataclass
class BatchResult:
    """Result of a single batch query."""
    query: BatchQuery
    success: bool
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    duration_ms: float = 0


class BatchProcessor:
    """Process multiple queries in batch."""
    
    def __init__(self, client):
        """
        Initialize batch processor.
        
        Args:
            client: FilmotClient instance
        """
        self.client = client
        self.results: List[BatchResult] = []
    
    def load_queries_from_file(self, filepath: str) -> List[BatchQuery]:
        """
        Load queries from a file.
        
        Supports:
        - .txt: One query per line
        - .json: Array of query objects
        - .csv: CSV with 'query' column and optional param columns
        
        Args:
            filepath: Path to query file
        
        Returns:
            List of BatchQuery objects
        """
        path = Path(filepath)
        
        if path.suffix == '.txt':
            return self._load_txt(path)
        elif path.suffix == '.json':
            return self._load_json(path)
        elif path.suffix == '.csv':
            return self._load_csv(path)
        else:
            raise ValueError(f"Unsupported file format: {path.suffix}")
    
    def _load_txt(self, path: Path) -> List[BatchQuery]:
        """Load queries from text file (one per line)."""
        queries = []
        with open(path, 'r', encoding='utf-8') as f:
            for i, line in enumerate(f, 1):
                line = line.strip()
                if line and not line.startswith('#'):
                    queries.append(BatchQuery(
                        query=line,
                        params={},
                        name=f"query_{i}"
                    ))
        return queries
    
    def _load_json(self, path: Path) -> List[BatchQuery]:
        """Load queries from JSON file."""
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        if isinstance(data, list):
            queries = []
            for i, item in enumerate(data, 1):
                if isinstance(item, str):
                    queries.append(BatchQuery(query=item, params={}, name=f"query_{i}"))
                elif isinstance(item, dict):
                    queries.append(BatchQuery(
                        query=item.get("query", ""),
                        params={k: v for k, v in item.items() if k not in ("query", "name")},
                        name=item.get("name", f"query_{i}")
                    ))
            return queries
        else:
            raise ValueError("JSON file must contain an array of queries")
    
    def _load_csv(self, path: Path) -> List[BatchQuery]:
        """Load queries from CSV file."""
        queries = []
        with open(path, 'r', encoding='utf-8', newline='') as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader, 1):
                query = row.pop('query', '')
                name = row.pop('name', f"query_{i}")
                # Convert empty strings to None
                params = {k: (v if v else None) for k, v in row.items()}
                queries.append(BatchQuery(query=query, params=params, name=name))
        return queries
    
    def process_queries(
        self, 
        queries: List[BatchQuery],
        progress_callback: Optional[Callable[[int, int, BatchResult], None]] = None
    ) -> List[BatchResult]:
        """
        Process a list of queries.
        
        Args:
            queries: List of BatchQuery objects
            progress_callback: Optional callback(current, total, result) for progress
        
        Returns:
            List of BatchResult objects
        """
        import time
        
        self.results = []
        total = len(queries)
        
        for i, query in enumerate(queries, 1):
            start_time = time.time()
            
            try:
                result = self.client.search_subtitles(
                    query=query.query,
                    **query.params
                )
                
                duration_ms = (time.time() - start_time) * 1000
                
                if "error" in result:
                    batch_result = BatchResult(
                        query=query,
                        success=False,
                        error=result["error"],
                        duration_ms=duration_ms
                    )
                else:
                    batch_result = BatchResult(
                        query=query,
                        success=True,
                        result=result,
                        duration_ms=duration_ms
                    )
            except Exception as e:
                batch_result = BatchResult(
                    query=query,
                    success=False,
                    error=str(e),
                    duration_ms=(time.time() - start_time) * 1000
                )
            
            self.results.append(batch_result)
            
            if progress_callback:
                progress_callback(i, total, batch_result)
        
        return self.results
    
    def export_results(self, filepath: str, format: str = "json") -> str:
        """
        Export batch results to file.
        
        Args:
            filepath: Output file path
            format: Output format ("json" or "csv")
        
        Returns:
            Path to created file
        """
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        
        if format == "json":
            return self._export_json(path)
        elif format == "csv":
            return self._export_csv(path)
        else:
            raise ValueError(f"Unsupported format: {format}")
    
    def _export_json(self, path: Path) -> str:
        """Export results as JSON."""
        export_data = {
            "exported_at": datetime.now().isoformat(),
            "total_queries": len(self.results),
            "successful": sum(1 for r in self.results if r.success),
            "failed": sum(1 for r in self.results if not r.success),
            "results": []
        }
        
        for r in self.results:
            result_dict = {
                "name": r.query.name,
                "query": r.query.query,
                "params": r.query.params,
                "success": r.success,
                "duration_ms": r.duration_ms
            }
            
            if r.success:
                videos = r.result.get("result", [])
                result_dict["total_results"] = r.result.get("totalresultcount", len(videos))
                result_dict["videos"] = videos
            else:
                result_dict["error"] = r.error
            
            export_data["results"].append(result_dict)
        
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(export_data, f, indent=2, ensure_ascii=False)
        
        return str(path)
    
    def _export_csv(self, path: Path) -> str:
        """Export results summary as CSV."""
        fieldnames = [
            "name", "query", "success", "result_count", 
            "duration_ms", "error"
        ]
        
        with open(path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            
            for r in self.results:
                row = {
                    "name": r.query.name,
                    "query": r.query.query,
                    "success": r.success,
                    "result_count": len(r.result.get("result", [])) if r.result else 0,
                    "duration_ms": round(r.duration_ms, 2),
                    "error": r.error or ""
                }
                writer.writerow(row)
        
        return str(path)
    
    def stats(self) -> Dict[str, Any]:
        """Get batch processing statistics."""
        if not self.results:
            return {"total": 0, "processed": False}
        
        successful = [r for r in self.results if r.success]
        failed = [r for r in self.results if not r.success]
        
        total_duration = sum(r.duration_ms for r in self.results)
        total_results = sum(
            len(r.result.get("result", [])) 
            for r in successful if r.result
        )
        
        return {
            "total_queries": len(self.results),
            "successful": len(successful),
            "failed": len(failed),
            "total_results": total_results,
            "total_duration_ms": round(total_duration, 2),
            "avg_duration_ms": round(total_duration / len(self.results), 2),
            "success_rate": round(len(successful) / len(self.results) * 100, 1)
        }


def create_batch_file_template(filepath: str, format: str = "json") -> str:
    """
    Create a template batch file.
    
    Args:
        filepath: Output file path
        format: File format ("json", "csv", or "txt")
    
    Returns:
        Path to created file
    """
    path = Path(filepath)
    
    if format == "json":
        template = [
            {"query": "machine learning tutorial", "lang": "en", "min_views": 10000},
            {"query": "python basics", "name": "python_search"},
            {"query": "data science", "category": "Education"},
            "simple query string"
        ]
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(template, f, indent=2)
    
    elif format == "csv":
        with open(path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(["query", "name", "lang", "min_views", "category"])
            writer.writerow(["machine learning", "ml_search", "en", "10000", ""])
            writer.writerow(["python tutorial", "py_search", "", "", "Education"])
            writer.writerow(["data science", "", "en", "", ""])
    
    elif format == "txt":
        with open(path, 'w', encoding='utf-8') as f:
            f.write("# One query per line\n")
            f.write("# Lines starting with # are comments\n")
            f.write("machine learning tutorial\n")
            f.write("python basics for beginners\n")
            f.write("data science projects\n")
    
    return str(path)
