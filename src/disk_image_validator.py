"""디스크 이미지 경로 및 형식 검증"""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path

from pydantic import BaseModel


class DiskImageFormat(str, Enum):
    """지원 디스크 이미지 형식"""

    E01 = "e01"
    DD = "dd"
    RAW = "raw"


EXTENSION_FORMAT_MAP: dict[str, DiskImageFormat] = {
    ".e01": DiskImageFormat.E01,
    ".dd": DiskImageFormat.DD,
    ".raw": DiskImageFormat.RAW,
    ".img": DiskImageFormat.RAW,
    ".001": DiskImageFormat.RAW,
}


class ImageValidationResult(BaseModel):
    """디스크 이미지 유효성 검증 결과

    Attributes:
        is_valid: 검증 통과 여부
        format: 감지된 이미지 형식 (유효 시)
        error_message: 오류 메시지 (실패 시)
        error_code: 오류 코드 (실패 시)
    """

    is_valid: bool
    format: DiskImageFormat | None = None
    error_message: str | None = None
    error_code: str | None = None


def _check_path_exists(path: Path) -> str | None:
    """경로 존재 여부 확인

    Args:
        path: 검사할 파일 경로

    Returns:
        오류 메시지 (문제 없으면 None)
    """
    if not path.exists():
        return f"지정된 경로에 파일이 존재하지 않습니다: {path}"
    if not path.is_file():
        return f"지정된 경로가 파일이 아닙니다: {path}"
    return None


def _check_access_permission(path: Path) -> str | None:
    """읽기 권한 확인

    Args:
        path: 검사할 파일 경로

    Returns:
        오류 메시지 (문제 없으면 None)
    """
    if not os.access(path, os.R_OK):
        return f"파일 접근 권한이 없습니다: {path}"
    return None


def _detect_format(path: Path) -> DiskImageFormat | None:
    """확장자 기반 디스크 이미지 형식 감지

    Args:
        path: 디스크 이미지 파일 경로

    Returns:
        감지된 형식 (미지원 시 None)
    """
    suffix = path.suffix.lower()
    return EXTENSION_FORMAT_MAP.get(suffix)


def validate_image_path(path: str | Path) -> ImageValidationResult:
    """디스크 이미지 경로 및 형식 검증

    경로 존재, 파일 여부, 읽기 권한, 지원 형식을 순차 검증

    Args:
        path: 디스크 이미지 파일 경로

    Returns:
        검증 결과
    """
    path = Path(str(path).strip("'\""))

    error = _check_path_exists(path)
    if error:
        code = "NOT_A_FILE" if path.exists() else "PATH_NOT_FOUND"
        return ImageValidationResult(
            is_valid=False, error_message=error, error_code=code
        )

    error = _check_access_permission(path)
    if error:
        return ImageValidationResult(
            is_valid=False, error_message=error, error_code="ACCESS_DENIED"
        )

    fmt = _detect_format(path)
    if fmt is None:
        return ImageValidationResult(
            is_valid=False,
            error_message=f"미지원 형식입니다. 지원 형식: E01, dd, raw ({path.suffix})",
            error_code="UNSUPPORTED_FORMAT",
        )

    return ImageValidationResult(is_valid=True, format=fmt)
