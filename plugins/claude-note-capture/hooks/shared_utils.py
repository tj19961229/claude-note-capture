#!/usr/bin/env python3
"""
Shared utilities for Claude Code hooks (Plugin Edition).
Author: tj
Date: 2025-10-23

Provides common functions for API calls, retry logic, queue management, and logging.
Supports multiple configuration sources for plugin installation.
"""

import sys
import os
import json
import time
import requests
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any


# ============================================================================
# Configuration Management
# ============================================================================

def get_plugin_data_dir() -> Path:
    """Get the plugin data directory for storing runtime files.

    Auto-detects whether plugin is installed via marketplace or directly,
    and returns the appropriate data directory.

    Returns:
        Path to plugin data directory (creates if doesn't exist)
    """
    # Detect installation type by checking current file path
    current_file = Path(__file__).resolve()

    # Check if installed via marketplace (path contains 'marketplaces')
    if "marketplaces" in current_file.parts:
        # Extract marketplace name from path
        # Path format: ~/.claude/plugins/marketplaces/{marketplace-name}/hooks/...
        parts = current_file.parts
        try:
            idx = parts.index("marketplaces")
            marketplace_name = parts[idx + 1]
            data_dir = Path.home() / ".claude" / "plugins" / "marketplaces" / marketplace_name / "data"
        except (ValueError, IndexError):
            # Fallback if path structure is unexpected
            data_dir = Path.home() / ".claude" / "plugins" / "claude-note-capture" / "data"
    else:
        # Direct installation (not via marketplace)
        data_dir = Path.home() / ".claude" / "plugins" / "claude-note-capture" / "data"

    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def load_config() -> Dict[str, Any]:
    """Load configuration with multi-source support for plugin installation.

    Configuration priority (highest to lowest):
    1. Environment variable: CLAUDE_NOTE_API_URL (for api_base_url only)
    2. User plugin config: ~/.claude/plugins/claude-note-capture/config.json
    3. Plugin bundled config: ../config/config.json (relative to this file)
    4. Default values

    Returns:
        Configuration dictionary with all settings

    Example config.json:
        {
            "api_base_url": "http://localhost:8000/api/v1",
            "max_retry_count": 10,
            "request_timeout": 10,
            "processing_timeout_minutes": 5
        }
    """
    # Default configuration
    default_config = {
        "api_base_url": "http://localhost:8000/api/v1",
        "max_retry_count": 10,
        "request_timeout": 10,
        "processing_timeout_minutes": 5
    }

    config = default_config.copy()

    # Try loading from multiple locations (in reverse priority order)
    config_locations = [
        Path(__file__).parent.parent / "config" / "config.json",  # Plugin bundled
        Path.home() / ".claude" / "plugins" / "claude-note-capture" / "config.json",  # User config
    ]

    for config_file in config_locations:
        if config_file.exists():
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    file_config = json.load(f)
                config.update(file_config)
                break  # Use first found config
            except (json.JSONDecodeError, Exception) as e:
                print(
                    f"Warning: Failed to load {config_file}: {e}",
                    file=sys.stderr
                )

    # Environment variable override (highest priority)
    if env_api_url := os.environ.get('CLAUDE_NOTE_API_URL'):
        config['api_base_url'] = env_api_url

    return config


# Load configuration (module-level, loaded once on import)
_CONFIG = load_config()

# Configuration values
API_BASE_URL = _CONFIG["api_base_url"]
MAX_RETRY_COUNT = _CONFIG["max_retry_count"]
REQUEST_TIMEOUT = _CONFIG["request_timeout"]
PROCESSING_TIMEOUT_MINUTES = _CONFIG["processing_timeout_minutes"]

# File paths - use plugin data directory for runtime files
_PLUGIN_DATA_DIR = get_plugin_data_dir()
LOG_FILE = _PLUGIN_DATA_DIR / "hooks.log"
PENDING_QUEUE_FILE = _PLUGIN_DATA_DIR / "pending_queue.jsonl"
PROCESSING_QUEUE_FILE = _PLUGIN_DATA_DIR / "processing_queue.jsonl"
FAILED_QUEUE_FILE = _PLUGIN_DATA_DIR / "failed_queue.jsonl"
LOCK_FILE = _PLUGIN_DATA_DIR / "queue_processor.lock"
PROCESSOR_LOG_FILE = _PLUGIN_DATA_DIR / "queue_processor.log"


def log_message(message: str, level: str = "INFO"):
    """Write log message to both stderr and log file.

    Args:
        message: Log message content
        level: Log level (INFO, WARNING, ERROR)
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"[{timestamp}] [{level}] {message}\n"

    # Write to stderr
    sys.stderr.write(log_line)
    sys.stderr.flush()

    # Write to log file
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(log_line)
    except Exception as e:
        sys.stderr.write(f"Failed to write to log file: {e}\n")


def call_api_with_retry(
    method: str,
    url: str,
    json_data: Optional[Dict[str, Any]] = None,
    max_retries: int = 3,
    timeout: Optional[int] = None
) -> tuple[bool, Optional[Dict[str, Any]]]:
    """Call API with exponential backoff retry.

    Args:
        method: HTTP method (GET, POST, PUT, etc.)
        url: Full API URL
        json_data: JSON payload (optional, not used for GET requests)
        max_retries: Maximum number of retry attempts
        timeout: Request timeout in seconds

    Returns:
        Tuple of (success: bool, response_data: dict or None)

    Examples:
        # GET request
        success, data = call_api_with_retry('GET', url)

        # POST request
        success, data = call_api_with_retry('POST', url, payload)
        if success:
            print(f"Created: {data['id']}")
    """
    # Use configured timeout if not specified
    if timeout is None:
        timeout = REQUEST_TIMEOUT

    for attempt in range(max_retries):
        try:
            log_message(
                f"API call attempt {attempt + 1}/{max_retries}: {method} {url}"
            )

            # Prepare request kwargs
            request_kwargs = {
                'method': method,
                'url': url,
                'timeout': timeout
            }

            # Only add json payload for non-GET requests
            if method.upper() != 'GET' and json_data is not None:
                request_kwargs['json'] = json_data

            response = requests.request(**request_kwargs)

            # Special handling for 409 Conflict (session already exists)
            if response.status_code == 409:
                log_message(
                    f"Resource already exists (409), treating as success",
                    "INFO"
                )
                return True, response.json() if response.text else None

            response.raise_for_status()

            # Success
            result_data = response.json() if response.text else None
            log_message(f"API call succeeded: {method} {url}")
            return True, result_data

        except requests.Timeout as e:
            log_message(
                f"API call timeout (attempt {attempt + 1}/{max_retries}): {e}",
                "WARNING"
            )

        except requests.ConnectionError as e:
            log_message(
                f"API connection error (attempt {attempt + 1}/{max_retries}): {e}",
                "WARNING"
            )

        except requests.HTTPError as e:
            status_code = e.response.status_code if e.response else 0

            # Don't retry on 4xx errors (except 409, 422, 429)
            if 400 <= status_code < 500 and status_code not in [409, 422, 429]:
                log_message(
                    f"API call failed with non-retryable error {status_code}: {e}",
                    "ERROR"
                )
                return False, None

            log_message(
                f"API call failed (attempt {attempt + 1}/{max_retries}): "
                f"HTTP {status_code}",
                "WARNING"
            )

        except requests.RequestException as e:
            log_message(
                f"API call failed (attempt {attempt + 1}/{max_retries}): {e}",
                "WARNING"
            )

        # Exponential backoff: 1s, 2s, 4s
        if attempt < max_retries - 1:
            wait_time = 2 ** attempt
            log_message(f"Retrying in {wait_time}s...")
            time.sleep(wait_time)

    # All retries failed
    log_message(f"API call failed after {max_retries} attempts", "ERROR")
    return False, None


def save_to_failed_queue(message_data: Dict[str, Any]):
    """Save failed message to retry queue.

    Args:
        message_data: Message data including session_id, message, timestamp, retry_count

    Example:
        save_to_failed_queue({
            'session_id': '...',
            'message': {'role': 'user', 'content': '...'},
            'timestamp': '2025-10-22T15:30:00',
            'retry_count': 0
        })
    """
    try:
        with open(FAILED_QUEUE_FILE, 'a', encoding='utf-8') as f:
            f.write(json.dumps(message_data) + '\n')
        log_message(
            f"Saved failed message to queue (session: {message_data['session_id']})"
        )
    except Exception as e:
        log_message(f"Failed to write to queue file: {e}", "ERROR")


def truncate_content(content: str, max_length: int = 10000) -> str:
    """Truncate content to max_length if needed.

    API schema constraint: content field max length is 10000 characters.

    Args:
        content: Content string to truncate
        max_length: Maximum allowed length

    Returns:
        Truncated content with notice if truncation occurred
    """
    if len(content) <= max_length:
        return content

    truncated = content[:max_length - 100]  # Reserve space for notice
    truncated += (
        f"\n\n[... Content truncated. Original length: {len(content)} chars, "
        f"showing first {len(truncated)} chars ...]"
    )
    return truncated


# ============================================================================
# Queue Management Functions (for async message processing)
# ============================================================================

def append_to_queue(queue_file: Path, message_data: Dict[str, Any]):
    """Atomically append a message to a JSONL queue file.

    Args:
        queue_file: Path to the queue file
        message_data: Message data to append

    Example:
        append_to_queue(PENDING_QUEUE_FILE, {
            'id': 'msg_123',
            'type': 'user_message',
            'session_id': '...',
            'message': {'role': 'user', 'content': '...'},
            'timestamp': '2025-10-22T15:30:00',
            'retry_count': 0,
            'status': 'pending'
        })
    """
    try:
        with open(queue_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(message_data) + '\n')
        log_message(f"Appended message {message_data.get('id')} to {queue_file.name}")
    except Exception as e:
        log_message(f"Failed to append to {queue_file.name}: {e}", "ERROR")
        raise


def read_queue(queue_file: Path) -> list[Dict[str, Any]]:
    """Read all messages from a JSONL queue file.

    Args:
        queue_file: Path to the queue file

    Returns:
        List of message dictionaries
    """
    if not queue_file.exists():
        return []

    messages = []
    try:
        with open(queue_file, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    messages.append(json.loads(line))
                except json.JSONDecodeError as e:
                    log_message(
                        f"Failed to parse line {line_num} in {queue_file.name}: {e}",
                        "WARNING"
                    )
        return messages
    except Exception as e:
        log_message(f"Failed to read {queue_file.name}: {e}", "ERROR")
        return []


def write_queue(queue_file: Path, messages: list[Dict[str, Any]]):
    """Overwrite a queue file with a list of messages.

    Args:
        queue_file: Path to the queue file
        messages: List of message dictionaries
    """
    try:
        with open(queue_file, 'w', encoding='utf-8') as f:
            for msg in messages:
                f.write(json.dumps(msg) + '\n')
        log_message(f"Wrote {len(messages)} messages to {queue_file.name}")
    except Exception as e:
        log_message(f"Failed to write {queue_file.name}: {e}", "ERROR")
        raise


def remove_from_queue(queue_file: Path, message_id: str):
    """Remove a message from a queue by its ID.

    Args:
        queue_file: Path to the queue file
        message_id: Message ID to remove
    """
    messages = read_queue(queue_file)
    filtered = [msg for msg in messages if msg.get('id') != message_id]

    if len(filtered) == len(messages):
        log_message(
            f"Message {message_id} not found in {queue_file.name}",
            "WARNING"
        )
        return

    write_queue(queue_file, filtered)
    log_message(f"Removed message {message_id} from {queue_file.name}")


def move_message(
    from_queue: Path,
    to_queue: Path,
    message_id: str,
    update_fields: Optional[Dict[str, Any]] = None
):
    """Move a message from one queue to another, optionally updating fields.

    Args:
        from_queue: Source queue file
        to_queue: Destination queue file
        message_id: Message ID to move
        update_fields: Optional dict of fields to update during move

    Example:
        move_message(
            PENDING_QUEUE_FILE,
            PROCESSING_QUEUE_FILE,
            'msg_123',
            {'status': 'processing', 'started_at': datetime.utcnow().isoformat()}
        )
    """
    messages = read_queue(from_queue)
    message = None

    # Find and remove from source queue
    filtered = []
    for msg in messages:
        if msg.get('id') == message_id:
            message = msg.copy()
            # Apply updates
            if update_fields:
                message.update(update_fields)
        else:
            filtered.append(msg)

    if message is None:
        log_message(
            f"Message {message_id} not found in {from_queue.name}",
            "WARNING"
        )
        return

    # Write back to source queue (without the message)
    write_queue(from_queue, filtered)

    # Append to destination queue
    append_to_queue(to_queue, message)

    log_message(
        f"Moved message {message_id} from {from_queue.name} to {to_queue.name}"
    )


def try_acquire_lock(lock_file: Path, timeout: int = 0) -> bool:
    """Try to acquire a file-based lock.

    Args:
        lock_file: Path to the lock file
        timeout: How long to wait for the lock (seconds, 0 = no wait)

    Returns:
        True if lock was acquired, False otherwise

    Example:
        if try_acquire_lock(LOCK_FILE, timeout=5):
            try:
                # Do work
                pass
            finally:
                release_lock(LOCK_FILE)
    """
    import os
    import errno

    start_time = time.time()

    while True:
        try:
            # Try to create the lock file exclusively
            # O_CREAT | O_EXCL | O_RDWR creates file only if it doesn't exist
            fd = os.open(
                str(lock_file),
                os.O_CREAT | os.O_EXCL | os.O_RDWR
            )

            # Write PID to lock file
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)

            log_message(f"Acquired lock: {lock_file.name}")
            return True

        except OSError as e:
            if e.errno != errno.EEXIST:
                # Unexpected error
                log_message(f"Lock acquisition error: {e}", "ERROR")
                return False

            # Lock file exists, check if it's stale
            try:
                # Check if lock is stale (> 5 minutes old)
                if lock_file.exists():
                    age = time.time() - lock_file.stat().st_mtime
                    if age > 300:  # 5 minutes
                        log_message(
                            f"Removing stale lock (age: {age:.0f}s)",
                            "WARNING"
                        )
                        lock_file.unlink()
                        continue  # Try acquiring again
            except Exception:
                pass

            # Check timeout
            if timeout == 0:
                return False

            elapsed = time.time() - start_time
            if elapsed >= timeout:
                log_message(
                    f"Lock acquisition timeout after {elapsed:.1f}s",
                    "WARNING"
                )
                return False

            # Wait a bit before retrying
            time.sleep(0.1)


def release_lock(lock_file: Path):
    """Release a file-based lock.

    Args:
        lock_file: Path to the lock file
    """
    try:
        if lock_file.exists():
            lock_file.unlink()
            log_message(f"Released lock: {lock_file.name}")
    except Exception as e:
        log_message(f"Failed to release lock: {e}", "WARNING")


# ============================================================================
# Project Information Extraction Functions
# ============================================================================

def extract_project_id_from_transcript(transcript_path: str) -> str | None:
    """Extract Claude Code project ID from transcript path.

    Path format: ~/.claude/projects/{project-id}/{session-id}.jsonl

    Args:
        transcript_path: Path to the transcript file

    Returns:
        Project ID extracted from path, or None if extraction failed

    Example:
        >>> extract_project_id_from_transcript('~/.claude/projects/my-proj/sess-123.jsonl')
        'my-proj'
    """
    try:
        path = Path(transcript_path)
        parts = path.parts
        projects_idx = parts.index('projects')
        return parts[projects_idx + 1]
    except (ValueError, IndexError):
        return None


def _is_reasonable_project_id(project_id: str) -> bool:
    """Determine if a project_id is reasonable (not old path-based format).

    Args:
        project_id: The project_id to check

    Returns:
        True if the project_id looks reasonable, False if it looks like an old path format

    Examples:
        >>> _is_reasonable_project_id("claude_note-7f3a2b1c")
        True
        >>> _is_reasonable_project_id("-Users-xyn-X---project-claude-claude-note-claude-note")
        False
        >>> _is_reasonable_project_id("my-project")
        True
    """
    if not project_id:
        return False

    # Exclude obvious path formats (starts with '-' or contains too many '-')
    if project_id.startswith('-') or project_id.count('-') > 5:
        return False

    # Exclude overly long IDs (likely path conversions)
    if len(project_id) > 80:
        return False

    # Keep special legacy values
    if project_id == 'unknown-legacy-project':
        return True

    return True


def get_project_info_from_hook(hook_data: dict) -> dict:
    """Extract project information from hook data.

    Generates project_id in format: {project_name}-{hash8}
    For example: claude_note-7f3a2b1c

    Tries multiple methods to determine project identity:
    1. Extract from transcript_path (if reasonable format, use it)
    2. Generate new format: {project_name}-{path_hash[:8]}

    Args:
        hook_data: Hook payload dictionary containing cwd, transcript_path, etc.

    Returns:
        Dictionary with project_id, project_name, project_path, and source

    Example:
        >>> get_project_info_from_hook({
        ...     'cwd': '/Users/tj/my-project',
        ...     'transcript_path': '~/.claude/projects/my-project-7f3a2b1c/sess.jsonl'
        ... })
        {
            'project_id': 'my-project-7f3a2b1c',
            'project_name': 'my-project',
            'project_path': '/Users/tj/my-project',
            'source': 'transcript'
        }
    """
    import hashlib
    import re

    cwd = hook_data.get('cwd', '')
    transcript_path = hook_data.get('transcript_path', '')

    # Try to get cwd_path early for project name extraction
    cwd_path = Path(cwd).resolve() if cwd else None
    project_name = cwd_path.name if cwd_path else 'unknown'

    # Sanitize project name (remove special characters, limit length)
    project_name = re.sub(r'[^a-zA-Z0-9_-]', '_', project_name)
    if len(project_name) > 40:
        project_name = project_name[:40]

    # Method 1: Try to extract from transcript_path
    existing_id = extract_project_id_from_transcript(transcript_path)
    if existing_id and _is_reasonable_project_id(existing_id):
        # Already a reasonable project_id, keep it
        source = 'transcript'
        project_id = existing_id
    else:
        # Method 2: Generate new format: {project_name}-{hash[:8]}
        normalized_path = str(cwd_path) if cwd_path else cwd
        path_hash = hashlib.sha256(normalized_path.encode()).hexdigest()[:8]

        project_id = f"{project_name}-{path_hash}"
        source = 'generated'

    return {
        'project_id': project_id,
        'project_name': cwd_path.name if cwd_path else 'Unknown',
        'project_path': str(cwd_path) if cwd_path else cwd,
        'source': source
    }


def ensure_project_exists(project_info: dict) -> bool:
    """Ensure project exists in backend, create if not exists.

    Calls POST /api/v1/projects/ensure endpoint.

    Args:
        project_info: Dictionary with project_id, project_name, project_path, source

    Returns:
        True if project exists or was created successfully, False otherwise

    Example:
        >>> project_info = get_project_info_from_hook(hook_data)
        >>> if ensure_project_exists(project_info):
        ...     print("Project is ready")
    """
    url = f"{API_BASE_URL}/projects/ensure"
    payload = {
        'project_id': project_info['project_id'],
        'name': project_info['project_name'],
        'path': project_info['project_path'],
        'metadata': {'source': project_info['source']}
    }
    success, _ = call_api_with_retry('POST', url, payload)
    return success
