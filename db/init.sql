-- Forensic AI · 통합 DB 스키마 v2
-- PostgreSQL 16 + pgvector

CREATE EXTENSION IF NOT EXISTS vector;

-- updated_at 자동 갱신 트리거 함수
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ─────────────────────────────────────────
-- cases  (루트 테이블)
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cases (
  id               VARCHAR(36)   PRIMARY KEY DEFAULT gen_random_uuid()::text,
  title            VARCHAR(255)  NOT NULL,
  description      TEXT,
  user_prompt      TEXT,
  disk_image_path  VARCHAR(1024),
  analyst          VARCHAR(64),
  status           VARCHAR(20)   NOT NULL DEFAULT 'open',
  created_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
  updated_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE TRIGGER cases_updated_at
  BEFORE UPDATE ON cases
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ─────────────────────────────────────────
-- analysis_sessions  (cases 1:N)
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS analysis_sessions (
  id              SERIAL        PRIMARY KEY,
  case_id         VARCHAR(36)   NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
  phase           VARCHAR(20)   NOT NULL,   -- strategy|plan|execute|report
  system_profile  TEXT,
  strategy        TEXT,
  plan_text       TEXT,
  result_summary  TEXT,
  created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE INDEX ON analysis_sessions(case_id);

CREATE TRIGGER analysis_sessions_updated_at
  BEFORE UPDATE ON analysis_sessions
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ─────────────────────────────────────────
-- plan_steps  (analysis_sessions 1:N)
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS plan_steps (
  id           SERIAL        PRIMARY KEY,
  session_id   INTEGER       NOT NULL REFERENCES analysis_sessions(id) ON DELETE CASCADE,
  case_id      VARCHAR(36)   NOT NULL REFERENCES cases(id),
  step_index   INTEGER       NOT NULL,
  mcp_server   VARCHAR(64),
  purpose      TEXT,
  hints        TEXT,
  artifacts    JSONB,
  is_followup  BOOLEAN       NOT NULL DEFAULT FALSE,
  created_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
  UNIQUE(session_id, step_index)
);

CREATE INDEX ON plan_steps(session_id);
CREATE INDEX ON plan_steps(case_id);

-- ─────────────────────────────────────────
-- agent_run  (plan_steps 1:N)
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_run (
  id            SERIAL        PRIMARY KEY,
  session_id    INTEGER       NOT NULL REFERENCES analysis_sessions(id) ON DELETE CASCADE,
  plan_step_id  INTEGER       NOT NULL REFERENCES plan_steps(id) ON DELETE CASCADE,
  agent_name    VARCHAR(64)   NOT NULL,
  tool          VARCHAR(128),
  status        VARCHAR(20)   NOT NULL DEFAULT 'running',  -- running|success|error|partial|skip
  plan_round    INTEGER       NOT NULL DEFAULT 1,
  started_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
  finished_at   TIMESTAMPTZ
);

CREATE INDEX ON agent_run(session_id);
CREATE INDEX ON agent_run(plan_step_id);

-- ─────────────────────────────────────────
-- task_results  (agent_run 1:N)
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS task_results (
  id              SERIAL        PRIMARY KEY,
  agent_run_id    INTEGER       NOT NULL REFERENCES agent_run(id) ON DELETE CASCADE,
  case_id         VARCHAR(36)   NOT NULL REFERENCES cases(id),
  task_id         VARCHAR(36)   NOT NULL,
  status          VARCHAR(20)   NOT NULL,  -- running|success|error|partial|skip
  content         TEXT,
  output          TEXT,
  raw_output_ref  VARCHAR(1024),
  elapsed_ms      INTEGER,
  artifacts       JSONB,
  started_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
  completed_at    TIMESTAMPTZ,
  created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE INDEX ON task_results(agent_run_id);
CREATE INDEX ON task_results(case_id);

-- ─────────────────────────────────────────
-- dfxml_fragments  (task_results 1:N)
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dfxml_fragments (
  id              SERIAL       PRIMARY KEY,
  task_result_id  INTEGER      NOT NULL REFERENCES task_results(id) ON DELETE CASCADE,
  agent_name      VARCHAR(64),
  dfxml_fragment  TEXT         NOT NULL,
  created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX ON dfxml_fragments(task_result_id);

-- ─────────────────────────────────────────
-- reports  (cases 1:N)
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS reports (
  id           SERIAL       PRIMARY KEY,
  case_id      VARCHAR(36)  NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
  summary      TEXT,
  report_text  TEXT,
  dfxml        TEXT,
  status       VARCHAR(20)  NOT NULL DEFAULT 'draft',  -- draft|final|archived
  created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  updated_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX ON reports(case_id);

CREATE TRIGGER reports_updated_at
  BEFORE UPDATE ON reports
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ─────────────────────────────────────────
-- workflow_nodes  (cases 1:N · React Flow 캔버스)
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS workflow_nodes (
  id          SERIAL       PRIMARY KEY,
  case_id     VARCHAR(36)  NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
  x           DOUBLE PRECISION NOT NULL,
  y           DOUBLE PRECISION NOT NULL,
  updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX ON workflow_nodes(case_id);

CREATE TRIGGER workflow_nodes_updated_at
  BEFORE UPDATE ON workflow_nodes
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ─────────────────────────────────────────
-- workflow_edges  (cases 1:N · React Flow 캔버스)
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS workflow_edges (
  id           SERIAL       PRIMARY KEY,
  case_id      VARCHAR(36)  NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
  edge_id      VARCHAR(64)  NOT NULL,
  source_node  VARCHAR(64)  NOT NULL,
  target_node  VARCHAR(64)  NOT NULL,
  updated_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  UNIQUE(case_id, edge_id)
);

CREATE INDEX ON workflow_edges(case_id);

CREATE TRIGGER workflow_edges_updated_at
  BEFORE UPDATE ON workflow_edges
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ─────────────────────────────────────────
-- case_embedding  (pgvector RAG)
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS case_embedding (
  id         SERIAL       PRIMARY KEY,
  case_id    VARCHAR(36)  REFERENCES cases(id) ON DELETE SET NULL,
  phase      VARCHAR(20)  NOT NULL,
  content    TEXT         NOT NULL,
  embedding  VECTOR(1024) NOT NULL,
  created_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX ON case_embedding USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
