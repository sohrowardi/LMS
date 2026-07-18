"""Classify a directory-listing entry as folder / video / subtitle / other."""

VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v",
    ".mpg", ".mpeg", ".ts", ".m2ts", ".vob", ".3gp", ".ogv", ".rm", ".rmvb",
}

SUBTITLE_EXTENSIONS = {
    ".srt", ".ass", ".ssa", ".sub", ".vtt", ".idx", ".smi",
}


def classify(name: str, is_folder: bool) -> str:
    if is_folder:
        return "folder"
    ext = _extension(name)
    if ext in VIDEO_EXTENSIONS:
        return "video"
    if ext in SUBTITLE_EXTENSIONS:
        return "subtitle"
    return "other"


def _extension(name: str) -> str:
    idx = name.rfind(".")
    if idx == -1:
        return ""
    return name[idx:].lower()
