"""Fix: Add 'mode' field to videoGenModelConfig in video.py"""
import os, re

filepath = os.path.join("app", "services", "grok", "services", "video.py")

with open(filepath, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Add _mode_value method after _mode_flag
old_mode_flag = '''    @staticmethod
    def _mode_flag(preset: str) -> str:
        mode_map = {
            "fun": "--mode=extremely-crazy",
            "normal": "--mode=normal",
            "spicy": "--mode=extremely-spicy-or-crazy",
        }
        return mode_map.get(preset, "--mode=custom")'''

new_mode_flag = '''    @staticmethod
    def _mode_flag(preset: str) -> str:
        mode_map = {
            "fun": "--mode=extremely-crazy",
            "normal": "--mode=normal",
            "spicy": "--mode=extremely-spicy-or-crazy",
        }
        return mode_map.get(preset, "--mode=custom")

    @staticmethod
    def _mode_value(preset: str) -> str:
        """Return raw mode value for videoGenModelConfig."""
        mode_map = {
            "fun": "extremely-crazy",
            "normal": "normal",
            "spicy": "extremely-spicy-or-crazy",
        }
        return mode_map.get(preset, "custom")'''

for ending in ["\r\n", "\n"]:
    target = old_mode_flag.replace("\n", ending)
    if target in content:
        replacement = new_mode_flag.replace("\n", ending)
        content = content.replace(target, replacement, 1)
        print(f"Step 1: Added _mode_value method (ending={repr(ending)})")
        break
else:
    print("Step 1: FAILED - _mode_flag not found!")

# 2. Add mode to all videoGenModelConfig blocks
# Pattern: find "videoLength": video_length/video_length, and add mode after
count = 0
for pattern in [
    '"videoLength": video_length,\n',
    '"videoLength": video_length,\r\n',
]:
    parts = content.split(pattern)
    if len(parts) > 1:
        # Check how many we found
        found = len(parts) - 1
        print(f"Step 2: Found {found} videoGenModelConfig blocks to fix")
        # We need to insert mode after videoLength line
        # But we need the matching indentation
        # Let's do a regex approach instead
        break

# Use regex to find all videoGenModelConfig blocks and add mode
import re

def add_mode(match):
    global count
    indent = match.group(1)
    existing = match.group(0)
    # Check if mode already there
    if '"mode"' in existing:
        return existing
    count += 1
    # Add mode field before the closing }
    return existing.replace(
        f'{indent}    "videoLength": video_length,',
        f'{indent}    "videoLength": video_length,\n{indent}    "mode": self._mode_value(preset),'
    ).replace(
        f'{indent}    "videoLength": video_length,\r\n',
        f'{indent}    "videoLength": video_length,\r\n{indent}    "mode": self._mode_value(preset),\r\n'
    )

# Simpler approach: just replace the specific pattern
for ending in ["\r\n", "\n"]:
    old_gen = f'                    "videoLength": video_length,{ending}                }}{ending}            }}{ending}        }}'
    new_gen = f'                    "videoLength": video_length,{ending}                    "mode": self._mode_value(preset),{ending}                }}{ending}            }}{ending}        }}'
    
    replaced = content.replace(old_gen, new_gen)
    if replaced != content:
        replacements = content.count(old_gen)
        content = replaced
        print(f"Step 2: Added mode to {replacements} videoGenModelConfig blocks (ending={repr(ending)})")
        break
else:
    print("Step 2: FAILED - videoGenModelConfig pattern not found, trying alternative...")
    # Try to find and replace each individually
    # For generate function
    for ending in ["\r\n", "\n"]:
        old = f'"videoLength": video_length,{ending}'
        new = f'"videoLength": video_length,{ending}                    "mode": self._mode_value(preset),{ending}'
        if old in content:
            content = content.replace(old, new)
            count_r = content.count('"mode": self._mode_value(preset),')
            print(f"Step 2 alt: Added mode in {count_r} places")
            break

with open(filepath, "w", encoding="utf-8") as f:
    f.write(content)

print("Done!")
