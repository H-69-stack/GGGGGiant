import os
import tempfile
import logging
from typing import List, Tuple

from flask import Request
from werkzeug.datastructures import FileStorage

logger = logging.getLogger(__name__)


def validate_file_upload(file_storage: FileStorage, allowed_types: List[str]) -> Tuple[bool, str]:
    """
    Validate an uploaded FileStorage object.
    """
    if file_storage is None or not file_storage.filename:
        return False, "未选择文件"

    file_extension = os.path.splitext(file_storage.filename)[1].lower()
    if file_extension not in allowed_types:
        return False, f"不支持的文件类型: {file_extension}。支持的类型: {', '.join(allowed_types)}"

    # Flask config MAX_CONTENT_LENGTH handles size, but check here too
    if hasattr(file_storage, 'content_length') and file_storage.content_length:
        if file_storage.content_length > 100 * 1024 * 1024:
            return False, "文件大小超过100MB限制"

    return True, ""


def save_uploaded_file(file_storage: FileStorage, temp_dir: str = None) -> str:
    """
    Save uploaded FileStorage to a temp file. Returns the saved path.
    """
    if temp_dir is None:
        temp_dir = tempfile.mkdtemp()

    file_path = os.path.join(temp_dir, file_storage.filename)
    file_storage.save(file_path)
    return file_path


def cleanup_important_files(file_paths: List[str]):
    """
    Remove temporary files.
    """
    for file_path in file_paths:
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception as e:
            logger.warning(f"删除临时文件失败: {file_path}, 错误: {e}")
