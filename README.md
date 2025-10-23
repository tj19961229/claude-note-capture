# Claude Note Capture Plugin

> 📝 Automatically capture and sync Claude Code sessions to your knowledge management system

A Claude Code plugin that captures user messages, assistant responses, tool calls, and bash execution results, then asynchronously syncs them to a backend API for knowledge management and session analysis.

## ✨ Features

- **🔄 Async Processing**: Non-blocking hook execution with background queue processing
- **📊 Complete Session Capture**:
  - User messages (UserPromptSubmit)
  - Assistant responses with tool call parsing (Stop)
  - High-level tool attempts (PreToolUse for ExitPlanMode, AskUserQuestion, etc.)
  - Bash command results with stdout/stderr (PostToolUse)
- **🛡️ Robust Retry Mechanism**: Automatic retry with exponential backoff for failed API calls
- **📁 Clean Data Organization**: All runtime files stored in `~/.claude/plugins/claude-note-capture/data/`
- **⚙️ Flexible Configuration**: Multi-source config support (environment variables, user config, bundled defaults)
- **🔌 Zero Config Interference**: Plugin system ensures no conflicts with user's existing hooks

## 📦 Installation

### Option 1: Install from Git Repository (Recommended)

```bash
# Install directly from GitHub
/plugin install https://github.com/your-username/claude-note-capture

# Configure your API endpoint
mkdir -p ~/.claude/plugins/claude-note-capture
cp ~/.claude/plugins/claude-note-capture/config/config.json.example \
   ~/.claude/plugins/claude-note-capture/config.json

# Edit the config with your API URL
nano ~/.claude/plugins/claude-note-capture/config.json
```

### Option 2: Install from Plugin Marketplace

```bash
# Add the marketplace (if you have one)
/plugin marketplace add your-github/claude-note-marketplace

# Install the plugin
/plugin install claude-note-capture

# Configure as above
```

### Option 3: Local Development Installation

```bash
# Clone or copy the plugin directory to Claude's plugins folder
cp -r claude-note-capture ~/.claude/plugins/

# Configure
cp ~/.claude/plugins/claude-note-capture/config/config.json.example \
   ~/.claude/plugins/claude-note-capture/config.json
nano ~/.claude/plugins/claude-note-capture/config.json
```

## ⚙️ Configuration

The plugin supports multiple configuration sources with the following priority:

1. **Environment Variable** (highest priority)
   ```bash
   export CLAUDE_NOTE_API_URL="http://your-api-server:8000/api/v1"
   ```

2. **User Config File**
   ```bash
   ~/.claude/plugins/claude-note-capture/config.json
   ```

3. **Plugin Bundled Config** (fallback)
   ```bash
   ~/.claude/plugins/claude-note-capture/config/config.json
   ```

4. **Default Values** (lowest priority)

### Configuration Options

```json
{
  "api_base_url": "http://localhost:8000/api/v1",
  "max_retry_count": 10,
  "request_timeout": 10,
  "processing_timeout_minutes": 5
}
```

| Option | Default | Description |
|--------|---------|-------------|
| `api_base_url` | `http://localhost:8000/api/v1` | Backend API endpoint |
| `max_retry_count` | `10` | Maximum retry attempts for failed API calls |
| `request_timeout` | `10` | HTTP request timeout in seconds |
| `processing_timeout_minutes` | `5` | Queue processor timeout in minutes |

## 🎮 Usage

Once installed and configured, the plugin works automatically in the background:

1. **Start a Claude Code session** - Session metadata captured automatically
2. **Chat with Claude** - All user and assistant messages saved
3. **Tool execution** - Tool calls and results captured
4. **Bash commands** - Command output and exit codes recorded

All data is queued locally and synced asynchronously to your configured API endpoint.

### Verify Plugin Status

```bash
# List installed plugins
/plugin list

# Check if claude-note-capture is enabled
/plugin status claude-note-capture

# View plugin data directory
ls -la ~/.claude/plugins/claude-note-capture/data/
```

### Enable/Disable Plugin

```bash
# Disable temporarily
/plugin disable claude-note-capture

# Re-enable
/plugin enable claude-note-capture

# Uninstall completely
/plugin uninstall claude-note-capture
```

## 📂 Directory Structure

```
~/.claude/plugins/claude-note-capture/
├── .claude-plugin/
│   └── plugin.json              # Plugin metadata
├── hooks/
│   ├── hooks.json               # Hook configuration
│   ├── save_user_message.py     # UserPromptSubmit hook
│   ├── save_assistant_message.py # Stop hook (with tool parsing)
│   ├── save_tool_call_attempt.py # PreToolUse hook
│   ├── save_bash_result.py      # PostToolUse hook for Bash
│   ├── shared_utils.py          # Shared utilities
│   ├── queue_manager.py         # Background queue processor
│   └── retry_failed_messages.py # Manual retry utility
├── config/
│   └── config.json.example      # Configuration template
├── data/                        # Runtime data (auto-created)
│   ├── hooks.log                # Hook execution log
│   ├── queue_processor.log      # Background processor log
│   ├── pending_queue.jsonl      # Messages awaiting processing
│   ├── processing_queue.jsonl   # Currently processing
│   └── failed_queue.jsonl       # Failed messages (for retry)
└── README.md                    # This file
```

## 🔧 Troubleshooting

### Check Logs

```bash
# View hook execution log
tail -f ~/.claude/plugins/claude-note-capture/data/hooks.log

# View queue processor log
tail -f ~/.claude/plugins/claude-note-capture/data/queue_processor.log
```

### Retry Failed Messages

```bash
# Manually retry failed API calls
python3 ~/.claude/plugins/claude-note-capture/hooks/retry_failed_messages.py
```

### Common Issues

**Problem**: Messages not reaching backend
- ✅ Check `api_base_url` in config.json
- ✅ Verify backend API is running and accessible
- ✅ Check logs for error messages
- ✅ Inspect `failed_queue.jsonl` for failed messages

**Problem**: Plugin not capturing events
- ✅ Verify plugin is enabled: `/plugin list`
- ✅ Check hooks.json configuration
- ✅ Review `hooks.log` for execution errors

## 🏗️ Architecture

### Async Queue Processing

The plugin uses a non-blocking architecture:

1. **Hook executes** (< 150ms) - Writes message to `pending_queue.jsonl`
2. **Background processor launches** - Detached process handles API calls
3. **Hook returns immediately** - No blocking of Claude Code workflow
4. **Retry mechanism** - Failed messages moved to `failed_queue.jsonl`

### Captured Events

| Hook Event | Trigger | Data Captured |
|------------|---------|---------------|
| `UserPromptSubmit` | User sends message | User message content, session metadata |
| `Stop` | Assistant completes response | Assistant message, parsed tool calls |
| `PreToolUse` | High-level tools executed | Tool name, input parameters |
| `PostToolUse` | Bash command completes | Command, stdout, stderr, exit code |

## 🤝 Contributing

Contributions welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Submit a pull request with clear description

## 📄 License

MIT License - See LICENSE file for details

## 👤 Author

**tj**

## 🔗 Related Projects

- [Claude Code Official Docs](https://docs.claude.com/en/docs/claude-code/plugins)
- [Claude Code Plugin Marketplace](https://github.com/anthropics/claude-code)

---

**Made with ❤️ for the Claude Code community**
