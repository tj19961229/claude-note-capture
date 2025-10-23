# Installation Guide

## Quick Start (5 minutes)

### Step 1: Install the Plugin

Choose one of these methods:

**Method A: From Local Directory**
```bash
# Copy plugin to Claude's plugins folder
cp -r claude-note-capture ~/.claude/plugins/
```

**Method B: From Git (when published)**
```bash
/plugin install https://github.com/your-username/claude-note-capture
```

### Step 2: Configure API Endpoint

```bash
# Create config from template
mkdir -p ~/.claude/plugins/claude-note-capture
cp ~/.claude/plugins/claude-note-capture/config/config.json.example \
   ~/.claude/plugins/claude-note-capture/config.json

# Edit the configuration
nano ~/.claude/plugins/claude-note-capture/config.json
```

Update `api_base_url` to your backend API:

```json
{
  "api_base_url": "http://your-server:8000/api/v1",
  "max_retry_count": 10,
  "request_timeout": 10,
  "processing_timeout_minutes": 5
}
```

### Step 3: Verify Installation

```bash
# Check plugin is recognized
/plugin list

# Should see: claude-note-capture (enabled)
```

### Step 4: Test Capture

Start a new Claude Code session and send a message. Then check:

```bash
# View captured events
cat ~/.claude/plugins/claude-note-capture/data/pending_queue.jsonl

# View processing log
tail -f ~/.claude/plugins/claude-note-capture/data/queue_processor.log
```

## Alternative: Environment Variable Configuration

If you prefer not to create a config file, you can set environment variables:

```bash
# Add to your ~/.bashrc or ~/.zshrc
export CLAUDE_NOTE_API_URL="http://your-server:8000/api/v1"

# Reload shell
source ~/.bashrc  # or source ~/.zshrc
```

## Backend API Requirements

Your backend API must implement these endpoints:

### POST /sessions
Create or update a session
```json
{
  "claude_session_id": "string",
  "project_name": "string",
  "cwd": "string",
  "git_branch": "string (optional)",
  "git_status": "string (optional)"
}
```

### POST /sessions/{session_id}/messages
Add a message to a session
```json
{
  "role": "user" | "assistant",
  "content": "string",
  "metadata": {
    "tool_calls": [...],  // For assistant messages
    "project_name": "string",
    "cwd": "string"
  }
}
```

## Troubleshooting

### Plugin Not Showing Up

```bash
# Verify directory structure
ls -la ~/.claude/plugins/claude-note-capture/

# Should contain:
# .claude-plugin/plugin.json
# hooks/hooks.json
# hooks/*.py
```

### Hooks Not Executing

```bash
# Check hooks configuration
cat ~/.claude/plugins/claude-note-capture/hooks/hooks.json

# Verify Python scripts are executable
chmod +x ~/.claude/plugins/claude-note-capture/hooks/*.py
```

### API Connection Issues

```bash
# Test API endpoint manually
curl -X POST http://localhost:8000/api/v1/sessions \
  -H "Content-Type: application/json" \
  -d '{"claude_session_id":"test","project_name":"test","cwd":"/tmp"}'

# Check failed messages
cat ~/.claude/plugins/claude-note-capture/data/failed_queue.jsonl

# Retry failed messages
python3 ~/.claude/plugins/claude-note-capture/hooks/retry_failed_messages.py
```

## Uninstallation

```bash
# Remove plugin
/plugin uninstall claude-note-capture

# Clean up data (optional)
rm -rf ~/.claude/plugins/claude-note-capture/
```

## Support

For issues or questions:
1. Check logs: `~/.claude/plugins/claude-note-capture/data/*.log`
2. Review queue files: `~/.claude/plugins/claude-note-capture/data/*.jsonl`
3. Open an issue on GitHub

---

Need help? See [README.md](README.md) for full documentation.
