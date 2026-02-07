"""AWS Transcribe fallback for when YouTube captions are unavailable."""

import os
import subprocess
import time
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from typing import Tuple, Optional
import json

try:
    import boto3
    import requests
    HAS_BOTO3 = True
except ImportError:
    HAS_BOTO3 = False

# AWS configuration
AWS_PROFILE_NAME = 'APIBoss'
AWS_REGION_NAME = 'us-east-1'
S3_BUCKET_NAME = 'gpttransscripts'


class AWSTranscribeError(Exception):
    """Raised when AWS Transcribe operation fails."""
    pass


def check_dependencies() -> Tuple[bool, str]:
    """Check if required dependencies are available.
    
    Returns:
        Tuple of (success, error_message)
    """
    if not HAS_BOTO3:
        return False, "boto3 and requests packages required: pip install boto3 requests"
    
    # Check for yt-dlp
    try:
        result = subprocess.run(
            ["yt-dlp", "--version"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode != 0:
            return False, "yt-dlp not found. Install with: pip install yt-dlp"
    except FileNotFoundError:
        return False, "yt-dlp not found. Install with: pip install yt-dlp"
    except subprocess.TimeoutExpired:
        return False, "yt-dlp check timed out"
    
    return True, ""


def download_audio(video_id: str, output_dir: Optional[str] = None) -> str:
    """Download audio from YouTube video as MP3.
    
    Args:
        video_id: YouTube video ID
        output_dir: Directory to save audio file (uses temp dir if None)
    
    Returns:
        Path to downloaded MP3 file
        
    Raises:
        AWSTranscribeError: If download fails
    """
    if output_dir is None:
        output_dir = tempfile.gettempdir()
    
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    output_template = str(output_path / f"{video_id}.%(ext)s")
    youtube_url = f"https://www.youtube.com/watch?v={video_id}"
    
    command = [
        "yt-dlp",
        "-x",  # Extract audio
        "--audio-format", "mp3",
        "-o", output_template,
        "--no-playlist",
        youtube_url
    ]
    
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout for download
        )
        if result.returncode != 0:
            raise AWSTranscribeError(f"yt-dlp failed: {result.stderr}")
    except subprocess.TimeoutExpired:
        raise AWSTranscribeError("Audio download timed out (5 minutes)")
    except FileNotFoundError:
        raise AWSTranscribeError("yt-dlp not found")
    
    mp3_path = output_path / f"{video_id}.mp3"
    if not mp3_path.exists():
        raise AWSTranscribeError(f"MP3 file not created: {mp3_path}")
    
    return str(mp3_path)


def upload_to_s3(file_path: str, s3_client, bucket_name: str) -> str:
    """Upload file to S3.
    
    Args:
        file_path: Local file path
        s3_client: boto3 S3 client
        bucket_name: S3 bucket name
    
    Returns:
        S3 URI (s3://bucket/key)
    """
    object_name = os.path.basename(file_path)
    s3_client.upload_file(file_path, bucket_name, object_name)
    return f"s3://{bucket_name}/{object_name}"


def start_transcription_job(
    transcribe_client,
    video_id: str,
    media_uri: str,
    identify_language: bool = True
) -> str:
    """Start AWS Transcribe job.
    
    Args:
        transcribe_client: boto3 Transcribe client
        video_id: Video ID for job naming
        media_uri: S3 URI of audio file
        identify_language: If True, auto-detect language. Otherwise uses en-US.
    
    Returns:
        Job name
        
    Raises:
        AWSTranscribeError: If job fails to start
    """
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')
    job_name = f"{video_id}_{timestamp}"
    
    job_params = {
        'TranscriptionJobName': job_name,
        'Media': {'MediaFileUri': media_uri},
        'MediaFormat': 'mp3',
    }
    
    if identify_language:
        # Auto language detection
        job_params['IdentifyLanguage'] = True
    else:
        job_params['LanguageCode'] = 'en-US'
    
    try:
        transcribe_client.start_transcription_job(**job_params)
        return job_name
    except transcribe_client.exceptions.ConflictException:
        raise AWSTranscribeError(f"Job name conflict: {job_name}")
    except Exception as e:
        raise AWSTranscribeError(f"Failed to start transcription job: {e}")


def wait_for_transcription(
    transcribe_client,
    job_name: str,
    timeout: int = 600,
    poll_interval: int = 5,
    callback=None
) -> str:
    """Wait for transcription job to complete.
    
    Args:
        transcribe_client: boto3 Transcribe client
        job_name: Transcription job name
        timeout: Maximum wait time in seconds
        poll_interval: Time between status checks
        callback: Optional callback function(status) for progress updates
    
    Returns:
        Transcript file URI
        
    Raises:
        AWSTranscribeError: If job fails or times out
    """
    start_time = time.time()
    
    while True:
        elapsed = time.time() - start_time
        if elapsed > timeout:
            raise AWSTranscribeError(f"Transcription timed out after {timeout} seconds")
        
        response = transcribe_client.get_transcription_job(TranscriptionJobName=job_name)
        status = response['TranscriptionJob']['TranscriptionJobStatus']
        
        if callback:
            callback(status)
        
        if status == 'COMPLETED':
            return response['TranscriptionJob']['Transcript']['TranscriptFileUri']
        elif status == 'FAILED':
            failure_reason = response['TranscriptionJob'].get('FailureReason', 'Unknown')
            raise AWSTranscribeError(f"Transcription failed: {failure_reason}")
        
        time.sleep(poll_interval)


def fetch_transcript_text(transcript_uri: str) -> str:
    """Fetch transcript text from AWS response URL.
    
    Args:
        transcript_uri: URL to transcript JSON
    
    Returns:
        Transcript text
    """
    response = requests.get(transcript_uri)
    response.raise_for_status()
    data = response.json()
    return data['results']['transcripts'][0]['transcript']


def cleanup_job(transcribe_client, job_name: str) -> None:
    """Delete completed transcription job.
    
    Args:
        transcribe_client: boto3 Transcribe client
        job_name: Job name to delete
    """
    try:
        transcribe_client.delete_transcription_job(TranscriptionJobName=job_name)
    except Exception:
        pass  # Ignore cleanup errors


def cleanup_s3_file(s3_client, bucket_name: str, object_name: str) -> None:
    """Delete audio file from S3.
    
    Args:
        s3_client: boto3 S3 client
        bucket_name: S3 bucket name
        object_name: Object key to delete
    """
    try:
        s3_client.delete_object(Bucket=bucket_name, Key=object_name)
    except Exception:
        pass  # Ignore cleanup errors


def transcribe_video(
    video_id: str,
    identify_language: bool = True,
    cleanup: bool = True,
    progress_callback=None,
    audio_dir: Optional[str] = None,
    aws_profile: str = AWS_PROFILE_NAME,
    aws_region: str = AWS_REGION_NAME,
    s3_bucket: str = S3_BUCKET_NAME,
) -> Tuple[str, Optional[str]]:
    """Transcribe a YouTube video using AWS Transcribe.
    
    This is the main entry point for AWS fallback transcription.
    
    Args:
        video_id: YouTube video ID
        identify_language: Auto-detect language (True) or use en-US (False)
        cleanup: Clean up temporary files and AWS job after completion
        progress_callback: Optional callback function(stage, message)
        audio_dir: Directory for temporary audio files
        aws_profile: AWS profile name
        aws_region: AWS region
        s3_bucket: S3 bucket for audio storage
    
    Returns:
        Tuple of (transcript_text, detected_language or None)
        
    Raises:
        AWSTranscribeError: If transcription fails
    """
    # Check dependencies
    deps_ok, deps_msg = check_dependencies()
    if not deps_ok:
        raise AWSTranscribeError(deps_msg)
    
    def progress(stage: str, msg: str):
        if progress_callback:
            progress_callback(stage, msg)
    
    # Initialize AWS clients
    progress("init", "Initializing AWS clients...")
    session = boto3.Session(profile_name=aws_profile)
    s3_client = session.client('s3', region_name=aws_region)
    transcribe_client = session.client('transcribe', region_name=aws_region)
    
    mp3_path = None
    job_name = None
    s3_object_name = None
    
    try:
        # Download audio
        progress("download", f"Downloading audio for {video_id}...")
        mp3_path = download_audio(video_id, audio_dir)
        s3_object_name = os.path.basename(mp3_path)
        
        # Upload to S3
        progress("upload", "Uploading to S3...")
        media_uri = upload_to_s3(mp3_path, s3_client, s3_bucket)
        
        # Start transcription
        progress("transcribe", "Starting transcription job...")
        job_name = start_transcription_job(
            transcribe_client, video_id, media_uri, identify_language
        )
        
        # Wait for completion
        def status_callback(status):
            progress("transcribe", f"Job status: {status}")
        
        progress("transcribe", f"Waiting for transcription job: {job_name}")
        transcript_uri = wait_for_transcription(
            transcribe_client, job_name, callback=status_callback
        )
        
        # Fetch transcript
        progress("fetch", "Fetching transcript...")
        transcript_text = fetch_transcript_text(transcript_uri)
        
        # Get detected language if available
        detected_language = None
        if identify_language:
            response = transcribe_client.get_transcription_job(TranscriptionJobName=job_name)
            detected_language = response['TranscriptionJob'].get('LanguageCode')
        
        progress("complete", "Transcription complete!")
        return transcript_text, detected_language
        
    finally:
        # Cleanup
        if cleanup:
            if mp3_path and os.path.exists(mp3_path):
                try:
                    os.remove(mp3_path)
                except Exception:
                    pass
            
            if job_name:
                cleanup_job(transcribe_client, job_name)
            
            if s3_object_name:
                cleanup_s3_file(s3_client, s3_bucket, s3_object_name)
