from .call_security import CallSecurityManager, call_security_manager
import cloudinary
import cloudinary.uploader
import cloudinary.api
from fastapi import UploadFile, HTTPException
import os
import anyio
import re
import html
from functools import partial
from urllib.parse import quote
from typing import Tuple

cloudinary.config(secure=True)

ALLOWED_EXTENSIONS = {
    'image': {'png', 'jpg', 'jpeg', 'gif', 'webp'},
    'video': {'mp4', 'webm', 'mov', 'avi'},
    'audio': {'mp3', 'wav', 'ogg'},
}
MAX_FILE_SIZE = 25 * 1024 * 1024  # 25 MB

async def upload_file_to_cloudinary(
    file_content: bytes | str, filename: str
) -> Tuple[str, str]:
    """
    Uploads file content to Cloudinary and returns the secure URL and media type.
    `file_content` can be bytes or a path to a file.
    """
    file_extension = filename.split(".")[-1].lower()

    resource_type = "auto"
    media_type = "file"
    if file_extension in ALLOWED_EXTENSIONS["image"]:
        resource_type = "image"
        media_type = "image"
    elif file_extension in ALLOWED_EXTENSIONS["video"]:
        resource_type = "video"
        media_type = "video"
    elif file_extension in ALLOWED_EXTENSIONS["audio"]:
        resource_type = "video"  # Cloudinary uses 'video' for audio files
        media_type = "audio"

    # Use functools.partial to prepare the function with its keyword argument.
    # This is the fix for the "unexpected keyword argument 'resource_type'" error.
    upload_func = partial(cloudinary.uploader.upload, resource_type=resource_type)
    upload_result = await anyio.to_thread.run_sync(upload_func, file_content)

    secure_url = upload_result["secure_url"]

    if media_type == "file":
        # For generic files, add the attachment flag to force download with original filename
        # This improves the user experience for non-media files.
        parts = secure_url.split('/upload/')
        if len(parts) == 2:
            # URL-encode the filename to handle special characters like spaces
            encoded_filename = quote(filename, safe='')
            secure_url = f"{parts[0]}/upload/fl_attachment:{encoded_filename}/{parts[1]}"

    return secure_url, media_type

async def validate_and_save_file(file: UploadFile) -> Tuple[str, str]:
    """
    Validates an uploaded file by reading it, saves it to Cloudinary,
    and returns the URL and media type.
    Note: This function reads the entire file into memory to validate its size.
    """
    contents = await file.read()
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413, detail=f"File is too large. Max size is {MAX_FILE_SIZE // 1024 // 1024}MB."
        )

    # Since we've read the file, we can pass the contents directly to Cloudinary.
    return await upload_file_to_cloudinary(contents, file.filename)

def validate_email(email: str) -> bool:
    """Validate email format."""
    if not email:
        return False
    # A simple regex for email validation
    return re.match(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$", email) is not None

def validate_username(username: str) -> bool:
    """Validate username format."""
    if not username:
        return False
    # Allow letters, numbers, and underscores, 3-20 characters
    return re.match(r"^\w{3,20}$", username) is not None

def sanitize_text(text: str) -> str:
    """Sanitize text input to prevent XSS."""
    if not text:
        return ""
    return html.escape(text)
