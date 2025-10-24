#!/usr/bin/env python3
"""
Queue Manager for async message processing.
Author: tj
Date: 2025-10-23

Background process that processes messages from the pending queue:
1. Acquires file lock to ensure single instance
2. Processes messages from pending_queue.jsonl
3. Moves messages to processing_queue during processing
4. On success: removes from queue
5. On failure: increments retry_count, moves back to pending or failed queue
"""

import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

from shared_utils import (
    API_BASE_URL,
    MAX_RETRY_COUNT,
    PENDING_QUEUE_FILE,
    PROCESSING_QUEUE_FILE,
    FAILED_QUEUE_FILE,
    LOCK_FILE,
    PROCESSOR_LOG_FILE,
    call_api_with_retry,
    read_queue,
    move_message,
    remove_from_queue,
    try_acquire_lock,
    release_lock,
    log_message,
    truncate_content,
    ensure_project_exists,
    get_project_info_from_hook,
)


def save_message(session_id: str, message: Dict[str, str]) -> tuple[bool, int]:
    """Save message to session via API.

    Args:
        session_id: Session ID
        message: Message dict with 'role' and 'content'

    Returns:
        Tuple of (success: bool, status_code: int)
    """
    role = message.get('role')
    content = message.get('content', '')

    # Truncate content if needed
    truncated_content = truncate_content(content)

    message_payload = {
        'role': role,
        'content': truncated_content
    }

    log_message(
        f"Saving {role} message to session {session_id} "
        f"(length: {len(content)}, truncated: {len(truncated_content)})"
    )

    success, response, status_code = call_api_with_retry(
        method='POST',
        url=f"{API_BASE_URL}/sessions/{session_id}/messages",
        json_data=message_payload
    )

    if success:
        seq_num = response.get('sequence_number', 'unknown') if response else 'unknown'
        log_message(f"Message saved successfully (seq={seq_num})")
    else:
        log_message(f"Failed to save message (status={status_code})", "ERROR")

    return success, status_code


def create_session_fallback(session_id: str, metadata: Dict[str, Any]) -> bool:
    """Create session as fallback when message creation fails with 404.

    This function is only called when a message POST fails because the session
    doesn't exist. It extracts the session creation logic as a fallback mechanism.

    Args:
        session_id: Claude Code session ID
        metadata: Session metadata (cwd, project_name, project_id, etc.)

    Returns:
        True if session was created successfully
    """
    log_message(f"Creating session as fallback: {session_id}")

    project_name = metadata.get('project_name', 'Unknown Project')
    cwd = metadata.get('cwd', '')

    # Extract or generate project_id
    project_id = metadata.get('project_id', None)

    # If metadata doesn't have project_id, try to generate it from cwd
    if not project_id:
        from shared_utils import get_project_info_from_hook
        log_message("No project_id in metadata, generating from cwd", "WARNING")
        hook_data = {
            'cwd': cwd,
            'transcript_path': metadata.get('transcript_path', '')
        }
        project_info = get_project_info_from_hook(hook_data)
        project_id = project_info['project_id']
        log_message(f"Generated project_id: {project_id}")

    # Ensure project exists before creating session (defensive)
    project_info = {
        'project_id': project_id,
        'project_name': project_name,
        'project_path': cwd,
        'source': 'queue_manager_fallback'
    }

    if not ensure_project_exists(project_info):
        log_message(
            f"Failed to ensure project exists for session {session_id}",
            "WARNING"
        )
        # Continue anyway - project might already exist

    session_payload = {
        'id': session_id,
        'project_id': project_id,  # REQUIRED field for backend API
        'project_context': f"{project_name} (Claude Code)",
        'programming_language': 'Mixed',
        'metadata': {
            'source': 'claude_code',
            'cwd': cwd,
            'import_time': datetime.utcnow().isoformat()
        }
    }

    create_success, _, _ = call_api_with_retry(
        method='POST',
        url=f"{API_BASE_URL}/sessions",
        json_data=session_payload
    )

    if create_success:
        log_message(f"Session created successfully: {session_id}")
    else:
        log_message(f"Failed to create session: {session_id}", "ERROR")

    return create_success


def process_message(msg_data: Dict[str, Any]) -> tuple[bool, bool]:
    """Process a single message from the queue using optimistic execution.

    Optimistically tries to save the message first. If it fails with 404 (session
    not found), creates the session and retries. This reduces API calls by 50%
    in the normal case where SessionStart hook has already created the session.

    Args:
        msg_data: Message data from queue

    Returns:
        Tuple of (success: bool, is_permanent_failure: bool)
        - success: True if message was processed successfully
        - is_permanent_failure: True if failure is permanent (should move to failed queue immediately)
    """
    msg_id = msg_data.get('id', 'unknown')
    msg_type = msg_data.get('type')
    session_id = msg_data.get('session_id')
    message = msg_data.get('message', {})
    metadata = msg_data.get('metadata', {})

    log_message(f"Processing message {msg_id} (type={msg_type})")

    # Optimistic execution: try to save message directly
    success, status_code = save_message(session_id, message)

    if success:
        # Success on first try - the common case!
        log_message(f"✅ Successfully processed message {msg_id}")
        return True, False

    # Failed - check if it's because session doesn't exist
    if status_code == 404:
        log_message(f"Session {session_id} not found, creating as fallback")

        # Create session as fallback
        if not create_session_fallback(session_id, metadata):
            log_message(
                f"Failed to create session fallback for {session_id}, "
                "marking as permanent failure (session cannot be recreated)",
                "ERROR"
            )
            # IMPROVEMENT: Return permanent failure to avoid infinite retries
            # If we can't create the session, retrying won't help
            return False, True

        # Retry saving the message
        log_message(f"Retrying message save after session creation")
        success, status_code = save_message(session_id, message)

        if success:
            log_message(f"✅ Successfully processed message {msg_id} (after fallback)")
            return True, False
        else:
            log_message(
                f"Failed to save message even after session creation (status={status_code})",
                "WARNING"
            )
            return False, False
    else:
        # Failed for other reasons (not 404)
        log_message(f"Failed to save message (status={status_code}), will retry later", "WARNING")
        return False, False


def process_queue():
    """Process all messages in the pending queue."""
    log_message("=" * 80)
    log_message("Queue Processor Started")

    # Read pending queue
    pending_messages = read_queue(PENDING_QUEUE_FILE)

    if not pending_messages:
        log_message("No pending messages to process")
        return

    log_message(f"Found {len(pending_messages)} pending messages")

    processed_count = 0
    failed_count = 0
    moved_to_failed = 0

    for msg_data in pending_messages:
        msg_id = msg_data.get('id', str(uuid.uuid4()))
        retry_count = msg_data.get('retry_count', 0)

        log_message(f"Processing message {msg_id} (retry_count={retry_count})")

        # IMPROVEMENT: Check retry limit BEFORE processing to avoid wasting API calls
        if retry_count >= MAX_RETRY_COUNT:
            log_message(
                f"Message {msg_id} already exceeded max retries ({MAX_RETRY_COUNT}), "
                "moving to failed queue without processing",
                "ERROR"
            )
            move_message(
                PENDING_QUEUE_FILE,
                FAILED_QUEUE_FILE,
                msg_id,
                {
                    'status': 'failed',
                    'retry_count': retry_count,
                    'failed_at': datetime.utcnow().isoformat(),
                    'reason': 'max_retries_exceeded'
                }
            )
            moved_to_failed += 1
            continue

        # Move to processing queue
        move_message(
            PENDING_QUEUE_FILE,
            PROCESSING_QUEUE_FILE,
            msg_id,
            {
                'status': 'processing',
                'started_at': datetime.utcnow().isoformat()
            }
        )

        # Process the message
        success, is_permanent_failure = process_message(msg_data)

        if success:
            # Remove from processing queue (success)
            remove_from_queue(PROCESSING_QUEUE_FILE, msg_id)
            processed_count += 1
        else:
            # Processing failed
            retry_count += 1

            # IMPROVEMENT: Handle permanent failures immediately
            if is_permanent_failure:
                log_message(
                    f"Message {msg_id} encountered permanent failure "
                    "(session cannot be created), moving to failed queue",
                    "ERROR"
                )
                move_message(
                    PROCESSING_QUEUE_FILE,
                    FAILED_QUEUE_FILE,
                    msg_id,
                    {
                        'status': 'failed',
                        'retry_count': retry_count,
                        'failed_at': datetime.utcnow().isoformat(),
                        'reason': 'permanent_failure_session_creation_failed'
                    }
                )
                moved_to_failed += 1
            elif retry_count >= MAX_RETRY_COUNT:
                # Max retries reached, move to failed queue
                log_message(
                    f"Message {msg_id} exceeded max retries ({MAX_RETRY_COUNT}), "
                    "moving to failed queue",
                    "ERROR"
                )
                move_message(
                    PROCESSING_QUEUE_FILE,
                    FAILED_QUEUE_FILE,
                    msg_id,
                    {
                        'status': 'failed',
                        'retry_count': retry_count,
                        'failed_at': datetime.utcnow().isoformat(),
                        'reason': 'max_retries_exceeded'
                    }
                )
                moved_to_failed += 1
            else:
                # Move back to pending queue for retry
                log_message(
                    f"Message {msg_id} failed, moving back to pending queue "
                    f"(retry_count={retry_count})"
                )
                move_message(
                    PROCESSING_QUEUE_FILE,
                    PENDING_QUEUE_FILE,
                    msg_id,
                    {
                        'status': 'pending',
                        'retry_count': retry_count
                    }
                )
                failed_count += 1

    log_message(
        f"Queue processing completed: "
        f"{processed_count} processed, {failed_count} failed, "
        f"{moved_to_failed} moved to failed queue"
    )


def main():
    """Main entry point for queue processor."""
    try:
        # Try to acquire lock (don't wait)
        if not try_acquire_lock(LOCK_FILE, timeout=0):
            log_message(
                "Another processor instance is already running, exiting",
                "INFO"
            )
            sys.exit(0)

        try:
            # Process queue
            process_queue()
        finally:
            # Always release lock
            release_lock(LOCK_FILE)

    except Exception as e:
        log_message(f"Unexpected error: {e}", "ERROR")
        import traceback
        log_message(f"Traceback: {traceback.format_exc()}", "ERROR")
        sys.exit(1)


if __name__ == '__main__':
    main()
