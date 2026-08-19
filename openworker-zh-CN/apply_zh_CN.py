#!/usr/bin/env python3
"""
OpenWorker 中文汉化补丁
======================
将 OpenWorker 前端 UI 字符串替换为中文。

用法:
  1. git clone https://github.com/andrewyng/openworker.git
  2. cd openworker
  3. python3 /path/to/apply_zh_CN.py
  4. cd surfaces/gui && npm install && npx tauri build

兼容版本: OpenWorker v0.1.6
"""
import os
import re
import json
import sys

def find_src_dir():
    """Find the frontend source directory."""
    candidates = [
        os.path.join(os.getcwd(), "surfaces", "gui", "src"),
        os.path.join(os.getcwd(), "..", "surfaces", "gui", "src"),
    ]
    for c in candidates:
        if os.path.isdir(c):
            return c
    print("错误: 找不到 surfaces/gui/src 目录。请在 openworker 仓库根目录下运行此脚本。")
    sys.exit(1)

def load_translations():
    """Load zh-CN.json from the same directory as this script."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    trans_file = os.path.join(script_dir, "zh-CN.json")
    if not os.path.exists(trans_file):
        print(f"错误: 找不到翻译文件 {trans_file}")
        sys.exit(1)
    with open(trans_file, 'r', encoding='utf-8') as f:
        return json.load(f)

# 这些字符串是代码逻辑标识符，绝对不能翻译
DO_NOT_TRANSLATE = {
    "assistant", "tool", "connector", "user", "approval", "dirreq",
    "planreq", "question", "notice", "core", "optional", "mcp",
    "Promise", "Error:", "BETA", "OpenWorker", "Slack", "Gmail",
    "GitHub", "HubSpot", "MCP", "Coworker", "Chat", "Code",
}

def patch_file(filepath, translations):
    """Surgically patch a single file - only translate UI display strings."""
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    changed = False
    new_lines = []

    for line in lines:
        original_line = line
        stripped = line.strip()

        # Skip code-logic lines
        if any(x in stripped for x in [
            'kind:', 'kind ===', 'kind !==', '.kind', 'role ===', 'role !==',
            'q.set(', 'params.', 'category ===', 'tier ===', 'tier !==',
            'import ', 'export ', 'interface ', 'type ', 'const SURFACES',
            'visualFor(', 'ConnectorBadge', 'ConnectorIcon'
        ]):
            new_lines.append(line)
            continue

        sorted_keys = sorted(translations.keys(), key=len, reverse=True)

        for en in sorted_keys:
            zh = translations[en]

            # JSX text: >English<
            line = line.replace(f'>{en}<', f'>{zh}<')

            # Attributes
            line = line.replace(f'placeholder="{en}"', f'placeholder="{zh}"')
            line = line.replace(f'title="{en}"', f'title="{zh}"')
            line = line.replace(f'label="{en}"', f'label="{zh}"')
            line = line.replace(f'alt="{en}"', f'alt="{zh}"')

            # JSX expressions (only in JSX context)
            if any(x in line for x in ['className', '<span', '<div', '<button', '<p ', '<h1', '<h2', '<h3', '<label', '<a ']):
                line = line.replace(f'{{"{en}"}}', f'{{"{zh}"}}')
                line = line.replace(f"{{'{en}'}}", f"{{'{zh}'}}")

            # Standalone strings in JSX render context
            if any(x in line for x in ['className', '<span', '<div', '<button', 'return (', 'return(']):
                if f'"{en}"' in line and not any(x in line for x in ['===', '!==', 'kind', 'role', 'type', 'key:', 'value:']):
                    line = line.replace(f'"{en}"', f'"{zh}"')

        if line != original_line:
            changed = True
        new_lines.append(line)

    if changed:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)

    return changed

def main():
    translations = load_translations()
    # Remove dangerous entries
    translations = {k: v for k, v in translations.items() if k not in DO_NOT_TRANSLATE and len(k) >= 5}

    src_dir = find_src_dir()
    print(f"OpenWorker 中文汉化补丁")
    print(f"翻译条目: {len(translations)}")
    print(f"源码目录: {src_dir}")
    print()

    patched = 0
    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if 'node_modules' not in d and '__' not in d]
        for f in files:
            if not f.endswith(('.tsx', '.ts')) or '.test.' in f or '.spec.' in f:
                continue
            filepath = os.path.join(root, f)
            if patch_file(filepath, translations):
                patched += 1
                rel = os.path.relpath(filepath, src_dir)
                print(f"  ✓ {rel}")

    print(f"\n完成！已汉化 {patched} 个文件。")
    print("接下来请运行:")
    print("  cd surfaces/gui")
    print("  npm install")
    print('  sed -i \'\' \'s/"build": "tsc && vite build"/"build": "vite build"/\' package.json')
    print("  npx tauri build")

if __name__ == "__main__":
    main()
