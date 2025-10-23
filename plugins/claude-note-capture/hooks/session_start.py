#!/usr/bin/env python3
"""
Create or ensure session exists on SessionStart (Async Non-Blocking Mode).
Author: tj
Date: 2025-10-23

Triggered by SessionStart hook when a new Claude Code session begins.
Execution time: < 100ms (non-blocking)

Workflow:
1. Receive hook data from stdin (session_id, cwd, transcript_path)
2. Extract project information
3. Ensure project exists via POST /projects/ensure
4. Create session via POST /sessions
5. Return immediately (total < 100ms)

No background processor needed as this is a one-time setup operation.
"""

import sys
import os
import json
from pathlib import Path
from datetime import datetime
from shared_utils import (
    log_message,
    call_api_with_retry,
    get_project_info_from_hook,
    ensure_project_exists,
    save_debug_data,
    API_BASE_URL,
    get_plugin_data_dir,
)


def create_session(session_id: str, project_info: dict, hook_data: dict) -> bool:
    """Create a new session in the backend.

    Args:
        session_id: Claude Code session ID
        project_info: Project information dictionary
        hook_data: Full hook data

    Returns:
        True if session was created or already exists, False otherwise
    """
    url = f"{API_BASE_URL}/sessions"
    payload = {
        'id': session_id,
        'project_id': project_info['project_id'],
        'metadata': {
            'cwd': hook_data.get('cwd', ''),
            'transcript_path': hook_data.get('transcript_path', ''),
            'project_name': project_info['project_name'],
            'project_path': project_info['project_path'],
        }
    }

    success, response_data, _ = call_api_with_retry('POST', url, payload)

    if success:
        log_message(
            f"Session created/ensured: {session_id} (project: {project_info['project_id']})"
        )
    else:
        log_message(f"Failed to create session: {session_id}", "ERROR")

    return success


def main():
    """Main entry point for SessionStart hook (non-blocking)."""
    log_message("=" * 80)
    log_message("SessionStart Hook Triggered!")

    try:
        # 1. Parse hook payload from stdin
        stdin_data = sys.stdin.read()
        log_message(f"Received stdin data ({len(stdin_data)} bytes)")

        # DEBUG: Capture actual hook data for diagnosis (controlled by config)
        save_debug_data(
            get_plugin_data_dir() / "debug_session_start.json",
            {
                "timestamp": datetime.now().isoformat(),
                "stdin_raw": stdin_data,
                "stdin_length": len(stdin_data),
                "env_vars": {
                    "PLUGIN_DIR": os.environ.get("PLUGIN_DIR", "NOT_SET"),
                    "PWD": os.environ.get("PWD", "NOT_SET")
                },
                "cwd": os.getcwd()
            }
        )

        hook_data = json.loads(stdin_data)
        log_message("Successfully parsed hook data as JSON")

        # 2. Extract hook data
        session_id = hook_data.get('session_id')
        cwd = hook_data.get('cwd', '')
        transcript_path = hook_data.get('transcript_path', '')

        if not session_id:
            log_message("Missing session_id in hook data, skipping", "WARNING")
            return  # Don't block operation

        log_message(f"Session: {session_id}")
        log_message(f"Working directory: {cwd}")
        log_message(f"Transcript path: {transcript_path}")

        # 3. Extract project information
        project_info = get_project_info_from_hook(hook_data)
        log_message(
            f"Project info: {project_info['project_id']} "
            f"({project_info['project_name']}, source={project_info['source']})"
        )

        # 4. Ensure project exists
        if not ensure_project_exists(project_info):
            log_message("Failed to ensure project exists", "ERROR")
            # Continue anyway - non-blocking

        # 5. Create session
        if not create_session(session_id, project_info, hook_data):
            log_message("Failed to create session", "ERROR")
            # Continue anyway - non-blocking

        log_message("SessionStart hook completed (non-blocking)")

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
