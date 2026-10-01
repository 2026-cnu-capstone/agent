"""증거 이미지 메타데이터 API 라우터

브라우저는 로컬 파일을 볼 수 없으므로 분석 서버가 실제 파일을 읽어
세그먼트 구성과 EWF 볼륨 정보를 반환
"""

from __future__ import annotations

import re
import struct
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/evidence", tags=["evidence"])

EWF_SIGNATURE = b"EVF\x09\x0d\x0a\xff\x00"
EWF_FILE_HEADER_SIZE = 13
SECTION_DESCRIPTOR_SIZE = 76
COMPRESSION_LEVELS = {0: "none", 1: "fast", 2: "best"}
RAW_EXTENSIONS = {".dd", ".raw", ".img", ".001"}


def _ewf_segments(first: Path) -> list[Path]:
    """.E01과 같은 이름의 연속 세그먼트(.E02, .E03 ...) 수집

    Args:
        first: 첫 세그먼트(.E01) 경로

    Returns:
        이름순으로 정렬된 세그먼트 경로 목록
    """
    stem = first.stem
    pattern = re.compile(rf"^{re.escape(stem)}\.[Ee]\d{{2}}$")
    return sorted(p for p in first.parent.iterdir() if pattern.match(p.name))


def _read_ewf_volume(path: Path) -> dict[str, Any] | None:
    """첫 세그먼트의 volume/disk 섹션에서 매체 정보 파싱

    EWF-E01 볼륨 섹션 레이아웃 (libewf 문서 기준):
    chunk_count u32 @4, sectors_per_chunk u32 @8, bytes_per_sector u32 @12,
    sector_count u64 @16, compression_level u8 @52

    Args:
        path: 첫 세그먼트(.E01) 경로

    Returns:
        섹터·청크·논리 크기·압축 수준 dict, EWF가 아니거나 섹션이 없으면 None
    """
    with open(path, "rb") as f:
        if f.read(8) != EWF_SIGNATURE:
            return None
        offset = EWF_FILE_HEADER_SIZE
        for _ in range(64):
            f.seek(offset)
            desc = f.read(SECTION_DESCRIPTOR_SIZE)
            if len(desc) < SECTION_DESCRIPTOR_SIZE:
                return None
            section_type = desc[:16].rstrip(b"\x00")
            next_offset = struct.unpack_from("<Q", desc, 16)[0]
            if section_type in (b"volume", b"disk"):
                data = f.read(53)
                chunk_count, sectors_per_chunk, bytes_per_sector, sector_count = (
                    struct.unpack_from("<IIIQ", data, 4)
                )
                return {
                    "chunk_count": chunk_count,
                    "sectors_per_chunk": sectors_per_chunk,
                    "bytes_per_sector": bytes_per_sector,
                    "sector_count": sector_count,
                    "logical_size": sector_count * bytes_per_sector,
                    "compression": COMPRESSION_LEVELS.get(data[52], f"unknown({data[52]})"),
                }
            if next_offset <= offset:
                return None
            offset = next_offset
    return None


@router.get("/inspect")
async def inspect_evidence(path: str):
    """증거 이미지의 세그먼트 구성과 매체 정보 조회

    Args:
        path: 분석 서버 기준 증거 이미지 절대 경로

    Returns:
        포맷, 세그먼트 목록, 컨테이너 크기, 섹터·논리 크기 정보

    Raises:
        HTTPException: 파일 없음(404), EWF 헤더 해석 실패(422), 미지원 형식(415)
    """
    p = Path(path).expanduser()
    if not p.is_file():
        raise HTTPException(status_code=404, detail=f"파일을 찾을 수 없습니다: {path}")

    ext = p.suffix.lower()
    if ext == ".e01":
        segments = _ewf_segments(p)
        volume = _read_ewf_volume(p)
        if volume is None:
            raise HTTPException(status_code=422, detail="EWF 헤더를 해석할 수 없습니다")
        fmt = "e01"
    elif ext in RAW_EXTENSIONS:
        segments = [p]
        size = p.stat().st_size
        volume = {"bytes_per_sector": 512, "sector_count": size // 512, "logical_size": size}
        fmt = "raw"
    else:
        raise HTTPException(status_code=415, detail="지원 형식: .e01 .dd .raw .img .001")

    return {
        "path": str(p),
        "format": fmt,
        "segments": [{"name": s.name, "size": s.stat().st_size} for s in segments],
        "container_size": sum(s.stat().st_size for s in segments),
        **volume,
    }
