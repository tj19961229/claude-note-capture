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
)


def processor_log(message: str, level: str = "INFO"):
    """Write log message to processor log file and stderr.

    Args:
        message: Log message content
        level: Log level (INFO, WARNING, ERROR)
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"[{timestamp}] [{level}] {message}\n"

    # Write to stderr
    sys.stderr.write(log_line)
    sys.stderr.flush()

    # Write to processor log file
    try:
        with open(PROCESSOR_LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(log_line)
    except Exception as e:
        sys.stderr.write(f"Failed to write to processor log: {e}\n")


def create_or_get_session(session_id: str, metadata: Dict[str, Any]) -> bool:
    """Create session if it doesn't exist.

    Args:
        session_id: Claude Code session ID
        metadata: Session metadata (cwd, project_name, etc.)

    Returns:
        True if session exists or was created successfully
    """
    processor_log(f"Checking if session exists: {session_id}")

    # Step 1: Check if session already exists
    get_success, _ = call_api_with_retry(
        method='GET',
        url=f"{API_BASE_URL}/sessions/{session_id}",
        max_retries=1
    )

    if get_success:
        processor_log(f"Session already exists: {session_id}")
        return True

    # Step 2: Session doesn't exist, create it
    processor_log(f"Session not found, creating: {session_id}")

    project_name = metadata.get('project_name', 'Unknown Project')
    cwd = metadata.get('cwd', '')

    session_payload = {
        'id': session_id,
        'project_context': f"{project_name} (Claude Code)",
        'programming_language': 'Mixed',
        'metadata': {
            'source': 'claude_code',
            'cwd': cwd,
            'import_time': datetime.utcnow().isoformat()
        }
    }

    create_success, _ = call_api_with_retry(
        method='POST',
        url=f"{API_BASE_URL}/sessions",
        json_data=session_payload
    )

    if create_success:
        processor_log(f"Session created successfully: {session_id}")
    else:
        processor_log(f"Failed to create session: {session_id}", "ERROR")

    return create_success


def save_message(session_id: str, message: Dict[str, str]) -> bool:
    """Save message to session via API.

    Args:
        session_id: Session ID
        message: Message dict with 'role' and 'content'

    Returns:
        True if message was saved successfully
    """
    role = message.get('role')
    content = message.get('content', '')

    # Truncate content if needed
    truncated_content = truncate_content(content)

    message_payload = {
        'role': role,
        'content': truncated_content
    }

    processor_log(
        f"Saving {role} message to session {session_id} "
        f"(length: {len(content)}, truncated: {len(truncated_content)})"
    )

    success, response = call_api_with_retry(
        method='POST',
        url=f"{API_BASE_URL}/sessions/{session_id}/messages",
        json_data=message_payload
    )

    if success:
        seq_num = response.get('sequence_number', 'unknown') if response else 'unknown'
        processor_log(f"Message saved successfully (seq={seq_num})")
    else:
        processor_log(f"Failed to save message", "ERROR")

    return success


def process_message(msg_data: Dict[str, Any]) -> bool:
    """Process a single message from the queue.

    Args:
        msg_data: Message data from queue

    Returns:
        True if message was processed successfully
    """
    msg_id = msg_data.get('id', 'unknown')
    msg_type = msg_data.get('type')
    session_id = msg_data.get('session_id')
    message = msg_data.get('message', {})
    metadata = msg_data.get('metadata', {})

    processor_log(f"Processing message {msg_id} (type={msg_type})")

    # Ensure session exists
    if not create_or_get_session(session_id, metadata):
        processor_log(f"Failed to create/get session, will retry later", "WARNING")
        return False

    # Save message
    if not save_message(session_id, message):
        processor_log(f"Failed to save message, will retry later", "WARNING")
        return False

    processor_log(f"✅ Successfully processed message {msg_id}")
    return True


def process_queue():
    """Process all messages in the pending queue."""
    processor_log("=" * 80)
    processor_log("Queue Processor Started")

    # Read pending queue
    pending_messages = read_queue(PENDING_QUEUE_FILE)

    if not pending_messages:
        processor_log("No pending messages to process")
        return

    processor_log(f"Found {len(pending_messages)} pending messages")

    processed_count = 0
    failed_count = 0
    moved_to_failed = 0

    for msg_data in pending_messages:
        msg_id = msg_data.get('id', str(uuid.uuid4()))
        retry_count = msg_data.get('retry_count', 0)

        processor_log(f"Processing message {msg_id} (retry_count={retry_count})")

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
        success = process_message(msg_data)

        if success:
            # Remove from processing queue (success)
            remove_from_queue(PROCESSING_QUEUE_FILE, msg_id)
            processed_count += 1
        else:
            # Processing failed
            retry_count += 1

            if retry_count >= MAX_RETRY_COUNT:
                # Max retries reached, move to failed queue
                processor_log(
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
                        'failed_at': datetime.utcnow().isoformat()
                    }
                )
                moved_to_failed += 1
            else:
                # Move back to pending queue for retry
                processor_log(
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

    processor_log(
        f"Queue processing completed: "
        f"{processed_count} processed, {failed_count} failed, "
        f"{moved_to_failed} moved to failed queue"
    )


def main():
    """Main entry point for queue processor."""
    try:
        # Try to acquire lock (don't wait)
        if not try_acquire_lock(LOCK_FILE, timeout=0):
            processor_log(
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
        processor_log(f"Unexpected error: {e}", "ERROR")
        import traceback
        processor_log(f"Traceback: {traceback.format_exc()}", "ERROR")
        sys.exit(1)


if __name__ == '__main__':
    main()
