"""Fix video.py to skip re-uploading assets.grok.com images."""
import os

filepath = os.path.join("app", "services", "grok", "services", "video.py")

with open(filepath, "r", encoding="utf-8") as f:
    content = f.read()

old_block = """                if image_attachments:
                    upload_service = UploadService()
                    try:
                        for attach_data in image_attachments:
                            _, file_uri = await upload_service.upload_file(
                                attach_data, token
                            )
                            image_url = f"https://assets.grok.com/{file_uri}"
                            logger.info(f"Image uploaded for video: {image_url}")
                            break
                    finally:
                        await upload_service.close()"""

new_block = """                if image_attachments:
                    for attach_data in image_attachments:
                        if isinstance(attach_data, str) and attach_data.startswith("https://assets.grok.com/"):
                            image_url = attach_data
                            logger.info(f"Image already on assets.grok.com, skipping upload: {image_url}")
                            break
                        upload_service = UploadService()
                        try:
                            _, file_uri = await upload_service.upload_file(
                                attach_data, token
                            )
                            image_url = f"https://assets.grok.com/{file_uri}"
                            logger.info(f"Image uploaded for video: {image_url}")
                        finally:
                            await upload_service.close()
                        break"""

# Try both line endings
for ending in ["\r\n", "\n"]:
    target = old_block.replace("\n", ending)
    if target in content:
        replacement = new_block.replace("\n", ending)
        content = content.replace(target, replacement)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"SUCCESS: Replaced block (line ending: {repr(ending)})")
        break
else:
    print("ERROR: Old block not found in file!")
    # Debug: show what's actually there
    idx = content.find("if image_attachments:")
    if idx >= 0:
        print("Found 'if image_attachments:' at index", idx)
        print("Context:")
        print(repr(content[idx:idx+500]))
