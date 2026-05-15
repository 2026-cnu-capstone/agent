"""RAG 데이터 관리 인터랙티브 CLI

Usage:
    python scripts/rag_manage.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import select, delete, func

from config import load_settings
from database.engine import get_engine, init_db, get_session
from database.models import CaseEmbedding
from rag.embedding import Embedder
from rag.pgvector_store import PgVectorStore
from rag.service import RAGService
from rag.types import RAGQuery

DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
CYAN = "\033[36m"


def print_header() -> None:
    """타이틀 헤더 출력"""
    print(f"\n{BOLD}{'=' * 50}")
    print(f"  RAG 데이터 관리")
    print(f"{'=' * 50}{RESET}\n")


def print_menu() -> None:
    """메인 메뉴 출력"""
    print(f"  {BOLD}1{RESET}. 문서 목록 조회")
    print(f"  {BOLD}2{RESET}. 문서 상세 조회")
    print(f"  {BOLD}3{RESET}. 유사 문서 검색")
    print(f"  {BOLD}4{RESET}. 문서 추가")
    print(f"  {BOLD}5{RESET}. 문서 삭제")
    print(f"  {BOLD}6{RESET}. 전체 삭제")
    print(f"  {BOLD}q{RESET}. 종료\n")


def prompt_phase(required: bool = False) -> str | None:
    """phase 선택 프롬프트

    Args:
        required: 필수 입력 여부

    Returns:
        선택된 phase 문자열 또는 None
    """
    print(f"  {DIM}1) strategy  2) planning  3) execution  4) 전체{RESET}")
    while True:
        choice = input(f"  phase 선택 > ").strip()
        mapping = {"1": "strategy", "2": "planning", "3": "execution", "4": None, "": None}
        if choice in mapping:
            result = mapping[choice]
            if required and result is None:
                print(f"  {YELLOW}phase를 선택해야 합니다.{RESET}")
                continue
            return result
        print(f"  {YELLOW}1~4 중 선택하세요.{RESET}")


async def cmd_list(engine) -> None:
    """저장된 RAG 문서 목록 조회"""
    print(f"\n{CYAN}[문서 목록 조회]{RESET}")
    phase = prompt_phase()

    async with get_session(engine) as session:
        stmt = select(
            CaseEmbedding.id,
            CaseEmbedding.case_id,
            CaseEmbedding.phase,
            CaseEmbedding.content,
            CaseEmbedding.created_at,
        ).order_by(CaseEmbedding.id)

        if phase:
            stmt = stmt.where(CaseEmbedding.phase == phase)

        result = await session.execute(stmt)
        rows = result.all()

    if not rows:
        print(f"  {DIM}저장된 문서가 없습니다.{RESET}")
        return

    print(f"\n  {BOLD}{'ID':>4}  {'Case':>5}  {'Phase':<10}  {'Created':<17}  Content{RESET}")
    print(f"  {'-' * 80}")
    for row in rows:
        created = row.created_at.strftime("%Y-%m-%d %H:%M") if row.created_at else ""
        content_preview = row.content.replace("\n", " ")[:50]
        case_id = str(row.case_id) if row.case_id else "-"
        print(f"  {row.id:>4}  {case_id:>5}  {row.phase:<10}  {created:<17}  {content_preview}")

    print(f"\n  {GREEN}총 {len(rows)}건{RESET}")


async def cmd_get(engine) -> None:
    """특정 문서 상세 조회"""
    print(f"\n{CYAN}[문서 상세 조회]{RESET}")
    id_input = input("  문서 ID > ").strip()
    if not id_input.isdigit():
        print(f"  {YELLOW}유효한 ID를 입력하세요.{RESET}")
        return

    async with get_session(engine) as session:
        stmt = select(CaseEmbedding).where(CaseEmbedding.id == int(id_input))
        result = await session.execute(stmt)
        doc = result.scalar_one_or_none()

    if not doc:
        print(f"  {YELLOW}ID {id_input} 문서를 찾을 수 없습니다.{RESET}")
        return

    print(f"\n  {BOLD}ID{RESET}:       {doc.id}")
    print(f"  {BOLD}Case ID{RESET}:  {doc.case_id or '-'}")
    print(f"  {BOLD}Phase{RESET}:    {doc.phase}")
    print(f"  {BOLD}Created{RESET}:  {doc.created_at}")
    print(f"  {BOLD}Content{RESET}:")
    for line in doc.content.split("\n"):
        print(f"    {line}")


async def cmd_search(engine, embedder) -> None:
    """유사 문서 검색"""
    print(f"\n{CYAN}[유사 문서 검색]{RESET}")
    query_text = input("  검색 쿼리 > ").strip()
    if not query_text:
        print(f"  {YELLOW}쿼리를 입력하세요.{RESET}")
        return

    phase = prompt_phase()
    top_k_input = input(f"  결과 수 {DIM}(기본 5){RESET} > ").strip()
    top_k = int(top_k_input) if top_k_input.isdigit() else 5

    store = PgVectorStore(engine, embedder)
    query = RAGQuery(
        query=query_text,
        top_k=top_k,
        filters={"phase": phase} if phase else {},
    )

    print(f"  {DIM}검색 중...{RESET}", end="", flush=True)
    results = await store.search(query)
    print(f"\r  {GREEN}검색 완료{RESET}     ")

    if not results:
        print(f"  {DIM}검색 결과가 없습니다.{RESET}")
        return

    print(f"\n  {BOLD}{'#':>2}  {'ID':>4}  {'Case':>5}  {'Phase':<10}  {'Score':<7}  Content{RESET}")
    print(f"  {'-' * 80}")
    for i, r in enumerate(results, 1):
        content_preview = r.content.replace("\n", " ")[:45]
        score_color = GREEN if r.score >= 0.5 else YELLOW if r.score >= 0.3 else DIM
        print(
            f"  {i:>2}  {r.metadata.get('id', '?'):>4}  "
            f"{r.metadata.get('case_id', '-'):>5}  "
            f"{r.metadata.get('phase', ''):<10}  "
            f"{score_color}{r.score:<7.3f}{RESET}  {content_preview}"
        )


async def cmd_add(engine, embedder) -> None:
    """새 문서 추가"""
    print(f"\n{CYAN}[문서 추가]{RESET}")
    phase = prompt_phase(required=True)

    print(f"  {DIM}1) 직접 입력  2) 파일에서 읽기{RESET}")
    source = input("  입력 방식 > ").strip()

    content = ""
    if source == "2":
        file_path = input("  파일 경로 > ").strip()
        path = Path(file_path)
        if not path.exists():
            print(f"  {YELLOW}파일을 찾을 수 없습니다: {file_path}{RESET}")
            return
        content = path.read_text(encoding="utf-8").strip()
        print(f"  {DIM}{len(content)}자 읽음{RESET}")
    else:
        print(f"  {DIM}내용을 입력하세요 (빈 줄 입력 시 완료):{RESET}")
        lines = []
        while True:
            line = input("  ")
            if line == "":
                break
            lines.append(line)
        content = "\n".join(lines)

    if not content:
        print(f"  {YELLOW}내용이 비어있습니다.{RESET}")
        return

    case_id_input = input(f"  케이스 ID {DIM}(없으면 Enter){RESET} > ").strip()

    store = PgVectorStore(engine, embedder)
    metadata: dict[str, str] = {"phase": phase}
    if case_id_input.isdigit():
        metadata["case_id"] = case_id_input

    print(f"  {DIM}임베딩 생성 중...{RESET}", end="", flush=True)
    doc_id = await store.add_document(content=content, metadata=metadata)
    print(f"\r  {GREEN}문서 추가 완료{RESET} (ID: {doc_id}, phase: {phase}, {len(content)}자)")


async def cmd_delete(engine) -> None:
    """단건 문서 삭제"""
    print(f"\n{CYAN}[문서 삭제]{RESET}")
    id_input = input("  삭제할 문서 ID > ").strip()
    if not id_input.isdigit():
        print(f"  {YELLOW}유효한 ID를 입력하세요.{RESET}")
        return

    async with get_session(engine) as session:
        stmt = select(CaseEmbedding.id, CaseEmbedding.phase, CaseEmbedding.content).where(
            CaseEmbedding.id == int(id_input)
        )
        result = await session.execute(stmt)
        doc = result.one_or_none()

    if not doc:
        print(f"  {YELLOW}ID {id_input} 문서를 찾을 수 없습니다.{RESET}")
        return

    preview = doc.content.replace("\n", " ")[:60]
    print(f"  대상: [{doc.phase}] {preview}")
    confirm = input(f"  삭제하시겠습니까? (y/N) > ").strip().lower()
    if confirm != "y":
        print(f"  {DIM}취소{RESET}")
        return

    async with get_session(engine) as session:
        await session.execute(
            delete(CaseEmbedding).where(CaseEmbedding.id == int(id_input))
        )
        await session.commit()
    print(f"  {GREEN}삭제 완료{RESET}")


async def cmd_delete_all(engine) -> None:
    """전체 문서 삭제"""
    print(f"\n{CYAN}[전체 삭제]{RESET}")
    async with get_session(engine) as session:
        count_result = await session.execute(
            select(func.count()).select_from(CaseEmbedding)
        )
        count = count_result.scalar()

    if count == 0:
        print(f"  {DIM}삭제할 문서가 없습니다.{RESET}")
        return

    confirm = input(f"  {YELLOW}전체 {count}건을 삭제하시겠습니까?{RESET} (y/N) > ").strip().lower()
    if confirm != "y":
        print(f"  {DIM}취소{RESET}")
        return

    async with get_session(engine) as session:
        await session.execute(delete(CaseEmbedding))
        await session.commit()
    print(f"  {GREEN}{count}건 삭제 완료{RESET}")


async def main() -> None:
    """메인 루프"""
    settings = load_settings()
    engine = get_engine(settings.database_url)
    await init_db(engine)

    embedder = None

    def get_embedder() -> Embedder:
        nonlocal embedder
        if embedder is None:
            print(f"  {DIM}임베딩 모델 로드 중...{RESET}", end="", flush=True)
            embedder = Embedder(settings.rag.embedding_model)
            print(f"\r  {GREEN}모델 로드 완료{RESET}              ")
        return embedder

    print_header()

    while True:
        print_menu()
        choice = input(f"  선택 > ").strip().lower()

        if choice == "1":
            await cmd_list(engine)
        elif choice == "2":
            await cmd_get(engine)
        elif choice == "3":
            await cmd_search(engine, get_embedder())
        elif choice == "4":
            await cmd_add(engine, get_embedder())
        elif choice == "5":
            await cmd_delete(engine)
        elif choice == "6":
            await cmd_delete_all(engine)
        elif choice in ("q", "quit", "exit"):
            print("종료합니다.")
            break
        else:
            print(f"  {YELLOW}1~6 또는 q를 입력하세요.{RESET}")

        print()

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
