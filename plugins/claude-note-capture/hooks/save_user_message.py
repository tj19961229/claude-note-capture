#!/usr/bin/env python3
"""
Save user messages to backend API (Async Non-Blocking Mode).
Author: tj
Date: 2025-10-23

Triggered by UserPromptSubmit hook when user submits a prompt.
Execution time: < 100ms (non-blocking)

Workflow:
1. Receive hook data from stdin
2. Write message to pending queue (< 10ms)
3. Launch detached background processor (< 50ms)
4. Return immediately (total < 100ms)

The background processor handles actual API calls asynchronously.
"""

import sys
import os
import json
import uuid
import subprocess
from pathlib import Path
from datetime import datetime
from shared_utils import (
    log_message,
    truncate_content,
    append_to_queue,
    get_plugin_data_dir,
    get_project_info_from_hook,
    save_debug_data,
    launch_background_processor,
    PENDING_QUEUE_FILE,
)


def enqueue_user_message(claude_session_id: str, user_prompt: str, hook_data: dict):
    """Enqueue user message for async processing.

    Args:
        claude_session_id: Claude Code session ID
        user_prompt: User's prompt text
        hook_data: Full hook data for project extraction
    """
    # Extract project information
    project_info = get_project_info_from_hook(hook_data)

    # Create message data
    message_data = {
        'id': str(uuid.uuid4()),
        'type': 'user_message',
        'session_id': claude_session_id,
        'message': {
            'role': 'user',
            'content': truncate_content(user_prompt)
        },
        'metadata': {
            'project_id': project_info['project_id'],  # Include project_id for queue_manager
            'project_name': project_info['project_name'],
            'project_path': project_info['project_path'],
            'cwd': hook_data.get('cwd', '')
        },
        'timestamp': datetime.utcnow().isoformat(),
        'retry_count': 0,
        'status': 'pending'
    }

    # Append to pending queue
    append_to_queue(PENDING_QUEUE_FILE, message_data)

    log_message(
        f"User message queued for async processing (id={message_data['id']}, "
        f"session={claude_session_id}, project={message_data['metadata']['project_id']}, "
        f"length={len(user_prompt)})"
    )


def main():
    """Main entry point for UserPromptSubmit hook (non-blocking, fail-safe)."""
    log_message("=" * 80)
    log_message("UserPromptSubmit Hook Triggered (Async Mode)!")

    try:
        # 1. Parse hook payload from stdin
        stdin_data = sys.stdin.read()
        log_message(f"Received stdin data ({len(stdin_data)} bytes)")

        # DEBUG: Capture actual hook data for diagnosis (controlled by config)
        save_debug_data(
            get_plugin_data_dir() / "debug_user_prompt.json",
            {
                "timestamp": datetime.now().isoformat(),
                "stdin_raw": stdin_data,
                "stdin_length": len(stdin_data),
                "env_vars": {
                    "PLUGIN_DIR": os.environ.get("PLUGIN_DIR", "NOT_SET"),
                    "PWD": os.environ.get("PWD", "NOT_SET"),
                    "HOME": os.environ.get("HOME", "NOT_SET")
                },
                "python_info": {
                    "version": sys.version,
                    "executable": sys.executable,
                    "path": sys.path[:3]
                },
                "cwd": os.getcwd()
            }
        )

        hook_data = json.loads(stdin_data)
        log_message("Successfully parsed hook data as JSON")

        # 2. Extract hook data
        # FIX: Extract session_id from transcript_path (consistent with session_start.py)
        transcript_path = hook_data.get('transcript_path', '')

        if transcript_path:
            claude_session_id = Path(transcript_path).stem
            log_message(f"Session ID extracted from transcript_path: {claude_session_id}")
        else:
            claude_session_id = hook_data.get('session_id')
            log_message(f"Session ID from hook_data (transcript_path not available): {claude_session_id}", "WARNING")

        user_prompt = hook_data.get('prompt', '')
        cwd = hook_data.get('cwd', '')

        if not claude_session_id:
            log_message("Missing session_id (no transcript_path and no hook_data.session_id), skipping", "WARNING")
            return  # Don't block operation

        if not user_prompt:
            log_message("Empty user prompt, skipping", "WARNING")
            return

        log_message(f"Session: {claude_session_id}")
        log_message(f"User prompt: {user_prompt[:100]}...")
        log_message(f"Working directory: {cwd}")

        # 3. Enqueue message (< 10ms)
        enqueue_user_message(claude_session_id, user_prompt, hook_data)

        # 4. Launch background processor (< 50ms)
        launch_background_processor()

        # 5. Return immediately (total < 100ms)
        log_message("✅ UserPromptSubmit hook completed (async, non-blocking)")

    except json.JSONDecodeError as e:
        log_message(f"Failed to parse hook data: {e} (non-blocking)", "ERROR")
        # Don't block operation, just log the error
        return

    except Exception as e:
        log_message(f"Unexpected error: {e} (non-blocking)", "ERROR")
        import traceback
        log_message(f"Traceback: {traceback.format_exc()}", "ERROR")
        # Don't block operation, just log the error
        return


if __name__ == '__main__':
    try:
        main()
        sys.exit(0)  # Always return success
    except Exception as e:
        # Last resort: even if main() crashes, don't block operation
        print(f"Hook error (non-blocking): {e}", file=sys.stderr)
        sys.exit(0)  # Return 0 to not block operation
