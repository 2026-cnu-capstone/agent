# Forensic AI · 공유 DB 사용 가이드

> **DB 서버는 Oracle Cloud에 24시간 운영 중입니다.**  
> 팀원 누구나 아래 접속 정보만 설정하면 바로 사용할 수 있습니다.  
> 별도로 Docker를 설치하거나 실행할 필요 없습니다.

---

## 팀원 설정 (이것만 하면 됩니다)

`agent/.env` 파일을 열고 `DATABASE_URL`을 아래로 설정하세요.

```
DATABASE_URL=postgresql+asyncpg://forensic_ai:forensic_ai@168.107.39.168:5432/forensic_ai
```

---

## DB 접속 정보

| 항목 | 값 |
|------|-----|
| Host | `168.107.39.168` |
| Port | `5432` |
| Database | `forensic_ai` |
| User | `forensic_ai` |
| Password | `forensic_ai` |

---

## DB 테이블 및 데이터 확인 방법

### 방법 1 — pgAdmin 웹 UI (가장 쉬움)

브라우저에서 접속: **http://168.107.39.168:5050**

| 항목 | 값 |
|------|-----|
| 이메일 | `admin@forensic.ai` |
| 비밀번호 | `forensic_ai` |

**처음 접속 시 서버 등록 (1회만):**
1. 왼쪽 `Servers` 우클릭 → `Register` → `Server`
2. **일반** 탭: 이름 → `forensic_ai`
3. **연결** 탭:
   - 호스트: `forensic_ai_db`
   - 포트: `5432`
   - 데이터베이스: `forensic_ai`
   - 사용자명: `forensic_ai`
   - 비밀번호: `forensic_ai`
4. 저장

등록 후 `forensic_ai` → `Databases` → `forensic_ai` → `Schemas` → `Tables` 에서 테이블과 데이터를 볼 수 있습니다.

---

### 방법 2 — Python으로 빠른 확인

```python
import asyncio, asyncpg

async def check():
    conn = await asyncpg.connect(
        "postgresql://forensic_ai:forensic_ai@168.107.39.168:5432/forensic_ai"
    )
    rows = await conn.fetch("SELECT * FROM cases ORDER BY created_at DESC LIMIT 10")
    for r in rows:
        print(r)
    await conn.close()

asyncio.run(check())
```

---

## 테이블 목록 (v2 스키마)

```
cases                   포렌식 케이스 (루트)
├── analysis_sessions   분석 세션
│   └── plan_steps      분석 계획 단계
│       └── agent_run   Sub-Agent 실행 단위
│           └── task_results    실행 결과
│               └── dfxml_fragments   DFXML 단편
├── reports             최종 보고서
├── workflow_nodes      React Flow 노드 위치
├── workflow_edges      React Flow 엣지
└── case_embedding      pgvector RAG 임베딩
```

---

## 파일 구조 (참고용)

```
agent/
├── docker-compose.yml   # 서버에서 실행 중인 Docker 설정
├── db/
│   └── init.sql         # v2 스키마 SQL (테이블 10개)
└── DOCKER.md            # 이 파일
```
