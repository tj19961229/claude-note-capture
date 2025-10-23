#!/usr/bin/env python3
"""
Queue cleanup and recovery script.
Author: tj
Date: 2025-10-23

Periodically cleans up stale processing messages and moves max-retry messages to failed queue.
Should be run as a cron job (e.g., every 5 minutes).

Responsibilities:
1. Recover stuck messages in processing_queue (timeout > 5 minutes) → move to pending
2. Move messages with retry_count >= MAX_RETRY from pending → failed queue
3. Optionally trigger queue_manager to process pending messages

Usage:
    # Add to crontab
    */5 * * * * cd /path/to/claude_note && python3 .claude/hooks/retry_failed_messages.py
"""

import sys
import subprocess
from pathlib import Path
from datetime import datetime, timedelta
from shared_utils import (
    log_message,
    read_queue,
    move_message,
    MAX_RETRY_COUNT,
    PROCESSING_TIMEOUT_MINUTES,
    PENDING_QUEUE_FILE,
    PROCESSING_QUEUE_FILE,
    FAILED_QUEUE_FILE,
)


def recover_stuck_processing_messages() -> int:
    """Recover messages stuck in processing queue.

    Messages are considered stuck if:
    - They've been in processing_queue for > 5 minutes
    - The processor likely crashed or was killed

    Returns:
        Number of messages recovered
    """
    processing_messages = read_queue(PROCESSING_QUEUE_FILE)

    if not processing_messages:
        return 0

    log_message(f"Checking {len(processing_messages)} processing messages for timeouts")

    recovered_count = 0
    now = datetime.utcnow()

    for msg in processing_messages:
        msg_id = msg.get('id')
        started_at = msg.get('started_at')

        if not started_at:
            # No start time, move back to pending
            log_message(
                f"Message {msg_id} has no start time, moving to pending",
                "WARNING"
            )
            move_message(
                PROCESSING_QUEUE_FILE,
                PENDING_QUEUE_FILE,
                msg_id,
                {'status': 'pending'}
            )
            recovered_count += 1
            continue

        try:
            start_time = datetime.fromisoformat(started_at.replace('Z', '+00:00'))
            age = (now - start_time).total_seconds() / 60  # minutes

            if age > PROCESSING_TIMEOUT_MINUTES:
                log_message(
                    f"Message {msg_id} stuck in processing for {age:.1f} minutes, "
                    "moving back to pending"
                )
                move_message(
                    PROCESSING_QUEUE_FILE,
                    PENDING_QUEUE_FILE,
                    msg_id,
                    {'status': 'pending'}
                )
                recovered_count += 1
        except (ValueError, TypeError) as e:
            log_message(
                f"Failed to parse start time for message {msg_id}: {e}",
                "WARNING"
            )

    return recovered_count


def move_max_retry_to_failed() -> int:
    """Move messages that exceeded max retries to failed queue.

    Returns:
        Number of messages moved to failed queue
    """
    pending_messages = read_queue(PENDING_QUEUE_FILE)

    if not pending_messages:
        return 0

    moved_count = 0

    for msg in pending_messages:
        msg_id = msg.get('id')
        retry_count = msg.get('retry_count', 0)

        if retry_count >= MAX_RETRY_COUNT:
            log_message(
                f"Message {msg_id} exceeded max retries ({retry_count}), "
                "moving to failed queue"
            )
            move_message(
                PENDING_QUEUE_FILE,
                FAILED_QUEUE_FILE,
                msg_id,
                {
                    'status': 'failed',
                    'failed_at': datetime.utcnow().isoformat()
                }
            )
            moved_count += 1

    return moved_count


def trigger_queue_processor():
    """Trigger queue processor to process pending messages.

    Launches queue_manager.py in background to process any pending messages.
    """
    pending_messages = read_queue(PENDING_QUEUE_FILE)

    if not pending_messages:
        log_message("No pending messages to process")
        return

    log_message(f"Found {len(pending_messages)} pending messages, triggering processor")

    script_path = Path(__file__).parent / "queue_manager.py"

    try:
        subprocess.Popen(
            ['python3', str(script_path)],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL
        )
        log_message("Queue processor triggered")
    except Exception as e:
        log_message(f"Failed to trigger queue processor: {e}", "ERROR")


def main():
    """Main entry point for cleanup script."""
    log_message("=" * 80)
    log_message("Queue Cleanup Script Started")
    log_message(f"Timestamp: {datetime.now().isoformat()}")

    try:
        # 1. Recover stuck processing messages
        recovered = recover_stuck_processing_messages()
        if recovered > 0:
            log_message(f"✅ Recovered {recovered} stuck processing messages")

        # 2. Move max-retry messages to failed queue
        moved_to_failed = move_max_retry_to_failed()
        if moved_to_failed > 0:
            log_message(f"🗑️  Moved {moved_to_failed} max-retry messages to failed queue")

        # 3. Trigger queue processor if there are pending messages
        trigger_queue_processor()

        # 4. Summary
        log_message("=" * 80)
        log_message("Cleanup Summary:")
        log_message(f"  Recovered from processing: {recovered}")
        log_message(f"  Moved to failed: {moved_to_failed}")

        # Log queue stats
        pending_count = len(read_queue(PENDING_QUEUE_FILE))
        processing_count = len(read_queue(PROCESSING_QUEUE_FILE))
        failed_count = len(read_queue(FAILED_QUEUE_FILE))

        log_message(f"  Current queue stats:")
        log_message(f"    Pending: {pending_count}")
        log_message(f"    Processing: {processing_count}")
        log_message(f"    Failed: {failed_count}")

        log_message("Cleanup script completed")

    except Exception as e:
        log_message(f"Unexpected error in cleanup script: {e}", "ERROR")
        import traceback
        log_message(f"Traceback: {traceback.format_exc()}", "ERROR")
        sys.exit(1)


if __name__ == '__main__':
    main()
