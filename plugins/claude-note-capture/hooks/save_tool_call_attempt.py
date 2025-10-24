#!/usr/bin/env python3
"""
Save tool call attempts to backend API (Async Non-Blocking Mode).
Author: tj
Date: 2025-10-23

Triggered by PreToolUse hook BEFORE tool execution.
Captures ALL tool call attempts, including those that are later rejected.
Execution time: < 100ms (non-blocking)

Workflow:
1. Receive hook data from stdin (tool_name, tool_input, session_id)
2. Write tool call attempt to pending queue (< 10ms)
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


def enqueue_tool_call_attempt(
    claude_session_id: str,
    tool_name: str,
    tool_input: dict,
    hook_data: dict
):
    """Enqueue tool call attempt for async processing.

    Args:
        claude_session_id: Claude Code session ID
        tool_name: Name of the tool being called
        tool_input: Input parameters for the tool
        hook_data: Full hook data for project extraction
    """
    # Extract project information
    project_info = get_project_info_from_hook(hook_data)

    # Prepare content: tool name + key parameters
    content_parts = [f"🔧 Tool Call Attempt: {tool_name}"]

    # Add key parameters for important tools
    if tool_name == 'ExitPlanMode' and 'plan' in tool_input:
        plan_preview = tool_input['plan'][:500]
        content_parts.append(f"\n\n📋 Plan Preview:\n{plan_preview}...")
    elif tool_name == 'AskUserQuestion' and 'questions' in tool_input:
        questions = tool_input['questions']
        if questions:
            content_parts.append(f"\n\n❓ Questions: {len(questions)} question(s)")
            for i, q in enumerate(questions[:3], 1):  # Show first 3 questions
                question_text = q.get('question', 'N/A')[:100]
                content_parts.append(f"\n  {i}. {question_text}...")
    elif tool_name == 'SlashCommand' and 'command' in tool_input:
        cmd_preview = tool_input['command'][:200]
        content_parts.append(f"\n\n🔀 Slash Command: {cmd_preview}")
    elif tool_name == 'Skill' and 'command' in tool_input:
        skill_name = tool_input['command']
        content_parts.append(f"\n\n⚡ Skill: {skill_name}")
    elif tool_name in ['Read', 'Write', 'Edit'] and 'file_path' in tool_input:
        content_parts.append(f"\n\n📁 File: {tool_input['file_path']}")
    elif tool_name == 'Bash' and 'command' in tool_input:
        cmd_preview = tool_input['command'][:200]
        content_parts.append(f"\n\n💻 Command: {cmd_preview}...")

    full_content = '\n'.join(content_parts)

    # Create message data
    message_data = {
        'id': str(uuid.uuid4()),
        'type': 'tool_call_attempt',
        'session_id': claude_session_id,
        'message': {
            'role': 'assistant',  # Tool calls are initiated by assistant
            'content': truncate_content(full_content)
        },
        'metadata': {
            'project_id': project_info['project_id'],  # Include project_id for queue_manager
            'project_name': project_info['project_name'],
            'project_path': project_info['project_path'],
            'cwd': hook_data.get('cwd', ''),
            'tool_name': tool_name,
            'tool_input': tool_input,  # Store full tool input in metadata
            'tool_call_status': 'attempted',  # Status: attempted (not yet executed)
        },
        'timestamp': datetime.utcnow().isoformat(),
        'retry_count': 0,
        'status': 'pending'
    }

    # Append to pending queue
    append_to_queue(PENDING_QUEUE_FILE, message_data)

    log_message(
        f"Tool call attempt queued for async processing (id={message_data['id']}, "
        f"session={claude_session_id}, project={message_data['metadata']['project_id']}, "
        f"tool={tool_name})"
    )


def main():
    """Main entry point for PreToolUse hook (non-blocking)."""
    log_message("=" * 80)
    log_message("PreToolUse Hook Triggered (Async Mode)!")

    try:
        # 1. Parse hook payload from stdin
        stdin_data = sys.stdin.read()
        log_message(f"Received stdin data ({len(stdin_data)} bytes)")

        # DEBUG: Capture actual hook data for diagnosis (controlled by config)
        save_debug_data(
            get_plugin_data_dir() / "debug_pre_tool_use.json",
            {
                "timestamp": datetime.now().isoformat(),
                "stdin_raw": stdin_data,
                "stdin_length": len(stdin_data),
                "env_vars": {"PLUGIN_DIR": os.environ.get("PLUGIN_DIR", "NOT_SET")},
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

        tool_name = hook_data.get('tool_name', 'unknown')
        tool_input = hook_data.get('tool_input', {})
        cwd = hook_data.get('cwd', '')

        if not claude_session_id:
            log_message("Missing session_id (no transcript_path and no hook_data.session_id), skipping", "WARNING")
            return  # Don't block operation

        log_message(f"Session: {claude_session_id}")
        log_message(f"Tool: {tool_name}")
        log_message(f"Tool input keys: {list(tool_input.keys())}")
        log_message(f"Working directory: {cwd}")

        # 3. Enqueue tool call attempt (< 10ms)
        enqueue_tool_call_attempt(claude_session_id, tool_name, tool_input, hook_data)

        # 4. Launch background processor (< 50ms)
        launch_background_processor()

        # 5. Return successfully (non-blocking, allow tool execution)
        log_message("✅ PreToolUse hook completed (async, non-blocking)")

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
