#!/usr/bin/env python3
"""
Save Bash tool execution results to backend API (Async Non-Blocking Mode).
Author: tj
Date: 2025-10-23

Triggered by PostToolUse hook AFTER Bash command execution.
Captures command output (stdout/stderr), exit code, and execution time.
Execution time: < 100ms (non-blocking)

Workflow:
1. Receive hook data from stdin (tool_name, tool_input, tool_output, session_id)
2. Write tool execution result to pending queue (< 10ms)
3. Launch detached background processor (< 50ms)
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


def enqueue_bash_result(
    claude_session_id: str,
    tool_input: dict,
    tool_response: dict,
    cwd: str
):
    """Enqueue Bash execution result for async processing.

    Args:
        claude_session_id: Claude Code session ID
        tool_input: Input parameters (command, description, etc.)
        tool_response: Response from Bash execution (stdout, stderr, interrupted, etc.)
        cwd: Current working directory
    """
    project_name = Path(cwd).name if cwd else 'Unknown Project'

    # Extract command info
    command = tool_input.get('command', 'N/A')
    description = tool_input.get('description', '')

    # Extract execution results from tool_response (not tool_output!)
    stdout = tool_response.get('stdout', '')
    stderr = tool_response.get('stderr', '')
    interrupted = tool_response.get('interrupted', False)
    # Note: Claude Code doesn't provide exit_code in tool_response
    exit_code = 0 if stdout and not stderr and not interrupted else 1

    # Prepare content
    content_parts = [
        f"💻 Bash Execution Result",
        f"\n📝 Description: {description}" if description else "",
        f"\n⚡ Command: {command[:300]}{'...' if len(command) > 300 else ''}",
        f"\n📊 Exit Code: {exit_code} (estimated)",
        f"\n⚠️ Interrupted: {'Yes' if interrupted else 'No'}",
    ]

    # Add stdout preview
    if stdout:
        stdout_preview = stdout[:500]
        content_parts.append(f"\n\n📤 Output (stdout):\n{stdout_preview}{'...' if len(stdout) > 500 else ''}")
    else:
        content_parts.append("\n\n📤 Output (stdout): (empty)")

    # Add stderr if present
    if stderr:
        stderr_preview = stderr[:300]
        content_parts.append(f"\n\n❌ Error (stderr):\n{stderr_preview}{'...' if len(stderr) > 300 else ''}")

    # Filter out empty strings
    content_parts = [part for part in content_parts if part]
    full_content = '\n'.join(content_parts)

    # Create message data
    message_data = {
        'id': str(uuid.uuid4()),
        'type': 'tool_execution_result',
        'session_id': claude_session_id,
        'message': {
            'role': 'assistant',
            'content': truncate_content(full_content)
        },
        'metadata': {
            'project_name': project_name,
            'cwd': cwd,
            'tool_name': 'Bash',
            'tool_input': tool_input,
            'tool_response': {
                'stdout': stdout,
                'stderr': stderr,
                'interrupted': interrupted,
                'exit_code_estimated': exit_code,
            },
            'tool_call_status': 'completed',
        },
        'timestamp': datetime.utcnow().isoformat(),
        'retry_count': 0,
        'status': 'pending'
    }

    # Append to pending queue
    append_to_queue(PENDING_QUEUE_FILE, message_data)

    log_message(
        f"Bash execution result queued for async processing (id={message_data['id']}, "
        f"session={claude_session_id}, exit_code={exit_code})"
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
    """Main entry point for PostToolUse hook (non-blocking)."""
    log_message("=" * 80)
    log_message("PostToolUse Hook Triggered for Bash (Async Mode)!")

    try:
        # 1. Parse hook payload from stdin
        stdin_data = sys.stdin.read()
        log_message(f"Received stdin data ({len(stdin_data)} bytes)")

        # 🔍 DEBUG: Capture actual hook data for diagnosis
        try:
            debug_file = get_plugin_data_dir() / "debug_post_tool_use.json"
            debug_data = {
                "timestamp": datetime.now().isoformat(),
                "stdin_raw": stdin_data,
                "stdin_length": len(stdin_data),
                "env_vars": {"PLUGIN_DIR": os.environ.get("PLUGIN_DIR", "NOT_SET")},
                "cwd": os.getcwd()
            }
            with open(debug_file, 'w', encoding='utf-8') as f:
                json.dump(debug_data, f, indent=2, ensure_ascii=False)
            log_message(f"🔍 DEBUG: Captured PostToolUse data to {debug_file}")
        except Exception as debug_err:
            log_message(f"🔍 DEBUG: Failed to capture debug data: {debug_err}", "WARNING")

        hook_data = json.loads(stdin_data)
        log_message("Successfully parsed hook data as JSON")

        # 2. Extract hook data
        claude_session_id = hook_data.get('session_id')
        tool_name = hook_data.get('tool_name', 'unknown')
        tool_input = hook_data.get('tool_input', {})
        tool_response = hook_data.get('tool_response', {})  # ✅ Fixed: tool_response not tool_output
        cwd = hook_data.get('cwd', '')

        if not claude_session_id:
            log_message("Missing session_id in hook data, skipping", "WARNING")
            return  # Don't block operation

        if tool_name != 'Bash':
            log_message(f"Unexpected tool_name: {tool_name} (expected 'Bash')", "WARNING")

        log_message(f"Session: {claude_session_id}")
        log_message(f"Tool: {tool_name}")
        log_message(f"Command: {tool_input.get('command', 'N/A')[:100]}")
        log_message(f"Stdout length: {len(tool_response.get('stdout', ''))} bytes")
        log_message(f"Stderr length: {len(tool_response.get('stderr', ''))} bytes")
        log_message(f"Interrupted: {tool_response.get('interrupted', False)}")

        # 3. Enqueue bash result (< 10ms)
        enqueue_bash_result(claude_session_id, tool_input, tool_response, cwd)

        # 4. Launch background processor (< 50ms)
        launch_background_processor()

        # 5. Return successfully (non-blocking)
        log_message("✅ PostToolUse hook completed (async, non-blocking)")

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
