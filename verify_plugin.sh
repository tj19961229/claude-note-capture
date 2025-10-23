#!/bin/bash
# Plugin Verification Script
# Author: tj
# Date: 2025-10-23

echo "========================================"
echo "Claude Note Capture Plugin Verification"
echo "========================================"

PLUGIN_DIR="$(cd "$(dirname "$0")" && pwd)"
echo "Plugin directory: $PLUGIN_DIR"
echo ""

# Check required files
echo "Checking required files..."
REQUIRED_FILES=(
    ".claude-plugin/plugin.json"
    "hooks/hooks.json"
    "hooks/save_user_message.py"
    "hooks/save_assistant_message.py"
    "hooks/save_tool_call_attempt.py"
    "hooks/save_bash_result.py"
    "hooks/shared_utils.py"
    "hooks/queue_manager.py"
    "hooks/retry_failed_messages.py"
    "config/config.json.example"
    "README.md"
    "INSTALLATION.md"
)

MISSING=0
for file in "${REQUIRED_FILES[@]}"; do
    if [ -f "$PLUGIN_DIR/$file" ]; then
        echo "✓ $file"
    else
        echo "✗ $file (MISSING)"
        MISSING=$((MISSING + 1))
    fi
done

echo ""
if [ $MISSING -eq 0 ]; then
    echo "✅ All required files present"
else
    echo "❌ $MISSING file(s) missing"
    exit 1
fi

# Check Python scripts are valid
echo ""
echo "Checking Python scripts syntax..."
for script in "$PLUGIN_DIR/hooks"/*.py; do
    if python3 -m py_compile "$script" 2>/dev/null; then
        echo "✓ $(basename "$script")"
    else
        echo "✗ $(basename "$script") (SYNTAX ERROR)"
        exit 1
    fi
done

echo ""
echo "✅ All Python scripts have valid syntax"

# Check JSON files are valid
echo ""
echo "Checking JSON files..."
for json_file in "$PLUGIN_DIR/.claude-plugin/plugin.json" "$PLUGIN_DIR/hooks/hooks.json" "$PLUGIN_DIR/config/config.json.example"; do
    if python3 -c "import json; json.load(open('$json_file'))" 2>/dev/null; then
        echo "✓ $(basename "$json_file")"
    else
        echo "✗ $(basename "$json_file") (INVALID JSON)"
        exit 1
    fi
done

echo ""
echo "✅ All JSON files are valid"

# Display plugin metadata
echo ""
echo "========================================"
echo "Plugin Metadata"
echo "========================================"
python3 -c "
import json
with open('$PLUGIN_DIR/.claude-plugin/plugin.json') as f:
    meta = json.load(f)
    print(f\"Name: {meta['name']}\")
    print(f\"Version: {meta['version']}\")
    print(f\"Author: {meta['author']['name']}\")
    print(f\"Description: {meta['description'][:80]}...\")
"

echo ""
echo "========================================"
echo "✅ Plugin verification passed!"
echo "========================================"
echo ""
echo "Next steps:"
echo "1. Copy to Claude plugins folder:"
echo "   cp -r $PLUGIN_DIR ~/.claude/plugins/"
echo ""
echo "2. Configure API endpoint:"
echo "   cp ~/.claude/plugins/claude-note-capture/config/config.json.example \\"
echo "      ~/.claude/plugins/claude-note-capture/config.json"
echo "   nano ~/.claude/plugins/claude-note-capture/config.json"
echo ""
echo "3. Verify installation:"
echo "   /plugin list"
echo ""
