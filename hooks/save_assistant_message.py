#!/usr/bin/env python3
"""
Save assistant messages to backend API (Async Non-Blocking Mode).
Author: tj
Date: 2025-10-23

Triggered by Stop hook when Claude finishes responding.
Execution time: < 100ms (non-blocking)

Workflow:
1. Parse last assistant message from transcript (< 50ms)
2. Write message to pending queue (< 10ms)
3. Launch detached background processor (< 30ms)
4. Return immediately (total < 100ms)

The background processor handles actual API calls asynchronously.
"""

import sys
import json
import uuid
import subprocess
from pathlib import Path
from datetime import datetime
from shared_utils import (
    log_message,
    truncate_content,
    append_to_queue,
    PENDING_QUEUE_FILE,
)


def parse_last_assistant_message(transcript_path: Path) -> dict | None:
    """Parse the last assistant message from transcript JSONL file.

    Supports Claude Code transcript format with message blocks.
    Extracts both text content and tool usage information.

    Args:
        transcript_path: Path to transcript JSONL file

    Returns:
        Dictionary with 'text' and 'tool_calls' keys, or None if not found

    Example return value:
        {
            'text': 'Here is the analysis...',
            'tool_calls': [
                {'tool': 'ExitPlanMode', 'input': {'plan': '...'}},
                {'tool': 'Read', 'input': {'file_path': '...'}}
            ]
        }
    """
    if not transcript_path.exists():
        log_message(f"Transcript file not found: {transcript_path}", "WARNING")
        return None

    last_assistant_data = None

    try:
        with open(transcript_path, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    event = json.loads(line.strip())
                    event_type = event.get('type', '')

                    # Skip meta messages
                    if event.get('isMeta', False):
                        continue

                    # Claude Code format: type="assistant" with message object
                    if event_type == 'assistant':
                        message_obj = event.get('message', {})
                        content = message_obj.get('content', '')

                        # Handle content blocks (Claude API format)
                        if isinstance(content, list):
                            # Extract text blocks
                            text_parts = [
                                block.get('text', '')
                                for block in content
                                if block.get('type') == 'text'
                            ]
                            text_content = '\n'.join(text_parts)

                            # Extract tool use blocks
                            tool_calls = []
                            for block in content:
                                if block.get('type') == 'tool_use':
                                    tool_call = {
                                        'tool': block.get('name', 'unknown'),
                                        'input': block.get('input', {}),
                                        'id': block.get('id', '')
                                    }
                                    tool_calls.append(tool_call)

                            # Store data if there's any content
                            if text_content or tool_calls:
                                last_assistant_data = {
                                    'text': text_content,
                                    'tool_calls': tool_calls
                                }
                        elif isinstance(content, str) and content:
                            # Handle plain string content (backward compatibility)
                            last_assistant_data = {
                                'text': content,
                                'tool_calls': []
                            }

                except json.JSONDecodeError:
                    continue  # Skip malformed lines

    except Exception as e:
        log_message(f"Error parsing transcript: {e}", "ERROR")
        return None

    return last_assistant_data


def enqueue_assistant_message(
    claude_session_id: str,
    assistant_data: dict,
    cwd: str
):
    """Enqueue assistant message for async processing.

    Args:
        claude_session_id: Claude Code session ID
        assistant_data: Dictionary containing 'text' and 'tool_calls'
        cwd: Current working directory
    """
    project_name = Path(cwd).name if cwd else 'Unknown Project'

    text_content = assistant_data.get('text', '')
    tool_calls = assistant_data.get('tool_calls', [])

    # Prepare content: combine text and tool call summaries
    content_parts = []
    if text_content:
        content_parts.append(text_content)

    if tool_calls:
        tool_summary = "\n\n--- Tool Calls ---\n"
        for tool_call in tool_calls:
            tool_name = tool_call.get('tool', 'unknown')
            tool_input = tool_call.get('input', {})
            tool_summary += f"\n• {tool_name}"

            # Add key parameters for important tools
            if tool_name == 'ExitPlanMode' and 'plan' in tool_input:
                plan_preview = tool_input['plan'][:200]
                tool_summary += f"\n  Plan: {plan_preview}..."
            elif tool_name == 'AskUserQuestion' and 'questions' in tool_input:
                questions = tool_input['questions']
                if questions:
                    tool_summary += f"\n  Questions: {len(questions)} question(s)"
            elif tool_name in ['Read', 'Write', 'Edit'] and 'file_path' in tool_input:
                tool_summary += f"\n  File: {tool_input['file_path']}"
            elif tool_name == 'Bash' and 'command' in tool_input:
                tool_summary += f"\n  Command: {tool_input['command'][:100]}"

        content_parts.append(tool_summary)

    full_content = '\n'.join(content_parts)

    # Create message data
    message_data = {
        'id': str(uuid.uuid4()),
        'type': 'assistant_message',
        'session_id': claude_session_id,
        'message': {
            'role': 'assistant',
            'content': truncate_content(full_content)
        },
        'metadata': {
            'project_name': project_name,
            'cwd': cwd,
            'tool_calls': tool_calls  # Store full tool call data in metadata
        },
        'timestamp': datetime.utcnow().isoformat(),
        'retry_count': 0,
        'status': 'pending'
    }

    # Append to pending queue
    append_to_queue(PENDING_QUEUE_FILE, message_data)

    log_message(
        f"Assistant message queued for async processing (id={message_data['id']}, "
        f"session={claude_session_id}, text_length={len(text_content)}, "
        f"tool_calls={len(tool_calls)})"
    )


def launch_background_processor():
    """Launch detached background processor to handle the queue.

    Uses subprocess.Popen with start_new_session=True to create a
    completely independent process that survives parent exit.
    """
    script_path = Path(__file__).parent / "queue_manager.py"

    try:
        # Launch detached process
        subprocess.Popen(
            ['python3', str(script_path)],
            start_new_session=True,  # POSIX: detach from parent process
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL
        )
        log_message("Background processor launched")
    except Exception as e:
        log_message(f"Failed to launch background processor: {e}", "WARNING")
        # Not fatal - message is already in queue, can be processed later


def main():
    """Main entry point for Stop hook (non-blocking)."""
    log_message("=" * 80)
    log_message("Stop Hook Triggered (Async Mode)!")

    try:
        # 1. Parse hook payload from stdin
        stdin_data = sys.stdin.read()
        log_message(f"Received stdin data ({len(stdin_data)} bytes)")

        hook_data = json.loads(stdin_data)
        log_message("Successfully parsed hook data as JSON")

        # 2. Extract hook data
        claude_session_id = hook_data.get('session_id')
        transcript_path = Path(hook_data.get('transcript_path', ''))
        cwd = hook_data.get('cwd', '')

        if not claude_session_id:
            log_message("Missing session_id in hook data, skipping", "WARNING")
            return  # Don't block operation

        if not transcript_path or not transcript_path.exists():
            log_message(f"Transcript file not found: {transcript_path}, skipping", "WARNING")
            return  # Don't block operation

        log_message(f"Session: {claude_session_id}")
        log_message(f"Transcript: {transcript_path}")

        # 3. Parse last assistant message from transcript (< 50ms)
        assistant_data = parse_last_assistant_message(transcript_path)

        if not assistant_data:
            log_message("No assistant message found in transcript", "WARNING")
            return

        text_content = assistant_data.get('text', '')
        tool_calls = assistant_data.get('tool_calls', [])

        log_message(f"Assistant message text: {text_content[:100]}...")
        if tool_calls:
            log_message(f"Tool calls detected: {len(tool_calls)} tool(s)")
            for tool_call in tool_calls:
                log_message(f"  - {tool_call.get('tool', 'unknown')}")

        # 4. Enqueue message (< 10ms)
        enqueue_assistant_message(claude_session_id, assistant_data, cwd)

        # 5. Launch background processor (< 30ms)
        launch_background_processor()

        # 6. Return immediately (total < 100ms)
        log_message("✅ Stop hook completed (async, non-blocking)")

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
