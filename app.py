"""
MIMIC Research Agent — Flask Web Application.
Orchestrates 5 clinical research skills through a web interface.

Pages:
  Page 1: EDA results → "开始探索" button
  Page 2: Mode select + Hypothesis list → "开始实验" button
  Page 3: Cohort extraction → "提取队列" button
  Page 4: Statistical results → "生成报告" button
  Page 5: Generated manuscript
"""

import json
import os
import uuid
import sys
from pathlib import Path

import time
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, Response, stream_with_context
from flask_cors import CORS

# Load .env file if present
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))

from orchestrator import orchestrator
from db import save_eda_cache, get_eda_cache, save_ideas_pool, get_ideas_pool

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "mimic-research-agent-mvp-secret-key")
CORS(app)

DATA_DIR = os.environ.get("DATA_DIR", str(Path(__file__).parent / "data"))


def _get_session_id() -> str:
    """Get or create a session ID for the current user."""
    if "session_id" not in session:
        session["session_id"] = uuid.uuid4().hex[:16]
    return session["session_id"]


# ============================================================
# Page Routes
# ============================================================

@app.route("/")
def index():
    """Main page — the single-page app."""
    return render_template("index.html")


# ============================================================
# API Routes
# ============================================================

@app.route("/api/progress")
def api_progress():
    """Get current workflow progress."""
    sid = _get_session_id()
    return jsonify(orchestrator.get_progress(sid))


@app.route("/api/reset", methods=["POST"])
def api_reset():
    """Reset the session to start over."""
    sid = _get_session_id()
    orchestrator.reset_session(sid)
    session.pop("session_id", None)
    return jsonify({"status": "reset"})


# ---- Skill 1 ----

@app.route("/api/skill1/run", methods=["POST"])
def api_run_skill1():
    """Run Skill 1: EDA on the MIMIC data. Uses cache when available."""
    sid = _get_session_id()
    data_dir = request.json.get("data_dir", DATA_DIR) if request.is_json else DATA_DIR

    if not os.path.isdir(data_dir):
        return jsonify({"error": f"Data directory not found: {data_dir}"}), 400

    try:
        # Check cache first
        cached = get_eda_cache(data_dir)
        if cached:
            s = orchestrator.get_session(sid)
            s.skill1_output = cached
            s.data_dir = data_dir
            return jsonify(cached)

        output = orchestrator.run_skill1(sid, data_dir)
        save_eda_cache(data_dir, output)
        return jsonify(output)
    except Exception as e:
        return jsonify({"error": str(e), "skill": "skill1"}), 500


@app.route("/api/skill1/status")
def api_skill1_status():
    """Get Skill 1 output (cached)."""
    sid = _get_session_id()
    s = orchestrator.get_session(sid)
    if s.skill1_output:
        return jsonify(s.skill1_output)
    return jsonify({"status": "not_run"})


# ---- Skill 2 ----

@app.route("/api/skill2/run", methods=["POST"])
def api_run_skill2():
    """Run Skill 2 based on mode:
    - paper_reproduction: search real published papers
    - ai_assisted: generate novel research ideas
    - independent_exploration: user provides their own idea (see /api/skill2/submit-idea)
    """
    sid = _get_session_id()
    mode = request.json.get("mode", "ai_assisted") if request.is_json else "ai_assisted"

    valid_modes = ["paper_reproduction", "ai_assisted", "independent_exploration"]
    if mode not in valid_modes:
        return jsonify({"error": f"Invalid mode. Choose from: {valid_modes}"}), 400

    try:
        output = orchestrator.run_skill2(sid, mode)
        return jsonify(output)
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e), "skill": "skill2"}), 500


@app.route("/api/skill2/select-idea", methods=["POST"])
def api_select_idea():
    """Select a specific research idea or paper."""
    sid = _get_session_id()
    idea_id = request.json.get("idea_id", "") if request.is_json else ""

    if not idea_id:
        return jsonify({"error": "idea_id is required"}), 400

    result = orchestrator.select_idea(sid, idea_id)
    return jsonify(result)


@app.route("/api/ideas/shuffle", methods=["POST"])
def api_ideas_shuffle():
    """Cycle to next batch of 10 ideas from the pre-generated pool."""
    sid = _get_session_id()
    try:
        result = orchestrator.shuffle_ideas(sid)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ideas/pool")
def api_ideas_pool():
    """Get the cached ideas pool."""
    ideas = get_ideas_pool()
    return jsonify({"ideas": ideas, "count": len(ideas)})


@app.route("/api/skill2/submit-idea", methods=["POST"])
def api_submit_idea():
    """Independent Exploration mode: user submits their own research idea."""
    sid = _get_session_id()
    data = request.json if request.is_json else {}

    required_fields = ["title", "research_question", "hypothesis"]
    for field in required_fields:
        if not data.get(field):
            return jsonify({"error": f"'{field}' is required for independent exploration"}), 400

    idea = {
        "id": "USER001",
        "title": data.get("title", ""),
        "research_question": data.get("research_question", ""),
        "hypothesis": data.get("hypothesis", ""),
        "pico": {
            "P": data.get("p_population", ""),
            "I": data.get("p_exposure", ""),
            "C": data.get("p_comparator", ""),
            "O": data.get("p_outcome", ""),
        },
        "data_variables": data.get("data_variables", []),
        "suggested_method": data.get("suggested_method", ""),
        "target_paper_type": "original_research",
        "scores": {},
        "total_score": 0,
        "rationale": "User-submitted idea (independent exploration mode)",
        "source": "user",
    }

    orchestrator.set_user_idea(sid, idea)
    return jsonify({"status": "selected", "idea": idea})


# ---- Skill 3 (Cohort Extraction) ----

@app.route("/api/skill3/run", methods=["POST"])
def api_run_skill3():
    """Run cohort extraction for the selected idea."""
    sid = _get_session_id()

    try:
        output = orchestrator.run_skill3(sid)
        return jsonify(output)
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e), "skill": "skill3"}), 500


@app.route("/api/skill3/status")
def api_skill3_status():
    """Get cohort extraction output (cached)."""
    sid = _get_session_id()
    s = orchestrator.get_session(sid)
    if s.skill3_output:
        return jsonify(s.skill3_output)
    return jsonify({"status": "not_run"})


# ---- Skill 4 (Statistical Testing) ----

@app.route("/api/skill4/run", methods=["POST"])
def api_run_skill4():
    """Run Skill 4: Statistical testing for selected idea."""
    sid = _get_session_id()

    try:
        output = orchestrator.run_skill4(sid)
        return jsonify(output)
    except Exception as e:
        return jsonify({"error": str(e), "skill": "skill4"}), 500


@app.route("/api/skill4/status")
def api_skill4_status():
    """Get Skill 4 output (cached)."""
    sid = _get_session_id()
    s = orchestrator.get_session(sid)
    if s.skill4_output:
        return jsonify(s.skill4_output)
    return jsonify({"status": "not_run"})


# ---- Skill 5 (Report Generation) ----

@app.route("/api/skill5/run", methods=["POST"])
def api_run_skill5():
    """Run Skill 5: Generate the final manuscript."""
    sid = _get_session_id()

    try:
        manuscript = orchestrator.run_skill5(sid)
        return jsonify({"manuscript": manuscript, "skill": "skill5", "status": "completed"})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e), "skill": "skill5"}), 500


@app.route("/api/skill5/status")
def api_skill5_status():
    """Get Skill 5 output (cached)."""
    sid = _get_session_id()
    s = orchestrator.get_session(sid)
    if s.skill5_output:
        return jsonify({"manuscript": s.skill5_output, "skill": "skill5", "status": "completed"})
    return jsonify({"status": "not_run"})


# ---- Streaming Pipeline (SSE) ----

@app.route("/api/stream-pipeline", methods=["POST"])
def api_stream_pipeline():
    """Run a single skill with SSE streaming progress.
    The 'skill' parameter determines which skill to run: skill1, skill2, skill2_papers, skill3, skill4, skill5.
    Defaults to skill1 if not specified."""
    sid = _get_session_id()
    data = request.json if request.is_json else {}
    mode = data.get("mode", "ai_assisted")
    target_skill = data.get("skill", "skill1")

    def generate():
        orch = orchestrator
        s = orch.get_session(sid)
        import time as _time

        def emit(event_type, **kwargs):
            payload = json.dumps({"event": event_type, **kwargs}, ensure_ascii=False, default=str)
            return f"data: {payload}\n\n"

        try:
            skill = target_skill

            # === Skill 1 ===
            if skill == "skill1":
                s.data_dir = s.data_dir or DATA_DIR

                # Check cache first
                cached = get_eda_cache(s.data_dir)
                if cached and not s.skill1_output:
                    s.skill1_output = cached

                yield emit("skill_start", skill="skill1", message="开始数据探索分析...")
                yield emit("step", skill="skill1", step="scan", message="正在扫描数据表结构...")

                if not s.skill1_output:
                    from skills import Skill1EDA
                    _time.sleep(0.2)
                    yield emit("step", skill="skill1", step="assess", message="正在评估变量质量（缺失率、异常值）...")
                    s.skill1_output = Skill1EDA(s.data_dir).run()
                    save_eda_cache(s.data_dir, s.skill1_output)
                else:
                    _time.sleep(0.1)

                yield emit("step", skill="skill1", step="detect", message=f"发现 {s.skill1_output.get('tables_found', 0)} 张表、{s.skill1_output.get('total_patients_estimate', 0)} 名患者（已缓存）")
                yield emit("skill_complete", skill="skill1", data=s.skill1_output, message="数据探索完成 — 已就绪")
                yield emit("report_update", skill="skill1", report="eda", title="数据质量报告")
                yield emit("pipeline_complete", message="EDA完成。在右侧 EDA 标签查看报告。输入指令继续。")

            # === Skill 2 (Papers) ===
            elif skill == "skill2_papers":
                mode = "paper_reproduction"
                yield emit("skill_start", skill="skill2", message="开始搜索论文...")
                yield emit("step", skill="skill2", step="search", message="正在搜索相关真实论文...")
                orch.run_skill2(sid, "paper_reproduction")
                count = len(s.skill2_output.get("papers", [])) if s.skill2_output else 0
                yield emit("skill_complete", skill="skill2", data=s.skill2_output, message=f"搜索完成: {count} 篇论文 — 已就绪")
                yield emit("report_update", skill="skill2", report="ideas", title="候选论文")
                yield emit("pipeline_complete", message=f"文献检索完成。{count} 篇论文已就绪。请选择一个论文进行复现。")

            # === Skill 2 (AI Ideas) ===
            elif skill == "skill2":
                # Load from ideas pool (pre-generated 50 ideas, cached in DB)
                pool = get_ideas_pool()
                if not pool:
                    # First time: generate ideas via LLM
                    yield emit("skill_start", skill="skill2", message="首次生成研究选题池（50个）...")
                    yield emit("step", skill="skill2", step="generate", message="正在调用 AI 生成 50 个候选研究 idea...")
                    if not s.skill1_output:
                        from skills import Skill1EDA
                        s.data_dir = s.data_dir or DATA_DIR
                        s.skill1_output = Skill1EDA(s.data_dir).run()
                    orch.run_skill2(sid, "ai_assisted")
                    all_ideas = (s.skill2_output or {}).get("ideas", [])
                    if all_ideas:
                        save_ideas_pool(all_ideas)
                        pool = all_ideas
                else:
                    yield emit("skill_start", skill="skill2", message="加载研究选题池...")
                    yield emit("step", skill="skill2", step="load", message=f"从缓存加载 {len(pool)} 个预生成选题...")
                    _time.sleep(0.2)

                # Sort by total_score descending, take top 10
                pool_sorted = sorted(pool, key=lambda x: x.get("total_score", 0), reverse=True)
                batch_size = 10
                offset = s.ideas_batch_offset
                batch = pool_sorted[offset:offset + batch_size]

                s.skill2_output = {
                    "skill": "skill2_ideas", "status": "completed", "mode": "ai_assisted",
                    "ideas": batch, "total_ideas": len(pool_sorted),
                    "batch_offset": offset, "batch_size": batch_size,
                }
                yield emit("skill_complete", skill="skill2", data=s.skill2_output,
                          message=f"选题就绪: {len(batch)} 个候选 (共 {len(pool_sorted)} 个) — 已就绪")
                yield emit("report_update", skill="skill2", report="ideas", title="候选研究选题")
                yield emit("pipeline_complete", message=f"展示评分最高的 {len(batch)} 个选题。点击「换一批」查看更多。")

            # === Skill 3 (Cohort Extraction) ===
            elif skill == "skill3":
                if not s.selected_idea:
                    items = (s.skill2_output or {}).get("ideas") or (s.skill2_output or {}).get("papers") or []
                    if items:
                        orch.select_idea(sid, items[0]["id"])
                yield emit("skill_start", skill="skill3", message="开始提取研究队列...")
                yield emit("step", skill="skill3", step="parse", message="正在解析研究 idea 的变量需求...")
                _time.sleep(0.3)
                yield emit("step", skill="skill3", step="build", message="正在从 CSV 文件构建分析数据集...")
                _time.sleep(0.3)
                yield emit("step", skill="skill3", step="join", message="正在关联数据表并提取变量...")
                orch.run_skill3(sid)
                n_final = s.skill3_output.get("final_cohort_size", 0) if s.skill3_output else 0
                n_cols = s.skill3_output.get("n_columns", 0) if s.skill3_output else 0
                yield emit("skill_complete", skill="skill3", data=s.skill3_output,
                          message=f"队列提取完成: {n_final} 名患者, {n_cols} 个变量 — 已就绪")
                yield emit("report_update", skill="skill3", report="cohort", title="研究队列")
                yield emit("pipeline_complete", message=f"队列提取完成。{n_final} 名患者就绪。输入指令继续统计检验。")

            # === Skill 4 (Statistical Testing) ===
            elif skill == "skill4":
                if not s.selected_idea:
                    items = (s.skill2_output or {}).get("ideas") or (s.skill2_output or {}).get("papers") or []
                    if items:
                        orch.select_idea(sid, items[0]["id"])
                yield emit("skill_start", skill="skill4", message="开始统计检验...")
                yield emit("step", skill="skill4", step="prereq", message="正在执行前提校验（样本量、缺失率、正态性）...")
                _time.sleep(0.3)
                yield emit("step", skill="skill4", step="match", message="正在匹配统计方法...")
                _time.sleep(0.3)
                yield emit("step", skill="skill4", step="execute", message="正在执行统计检验...")
                orch.run_skill4(sid)
                n_results = len(s.skill4_output.get("results", [])) if s.skill4_output else 0
                yield emit("skill_complete", skill="skill4", data=s.skill4_output, message=f"统计检验完成: {n_results} 项分析 — 已就绪")
                yield emit("report_update", skill="skill4", report="stats", title="统计检验报告")
                yield emit("pipeline_complete", message=f"统计检验完成。{n_results} 项检验已执行。输入指令继续。")

            # === Skill 5 (Report Generation) ===
            elif skill == "skill5":
                yield emit("skill_start", skill="skill5", message="开始生成报告...")
                yield emit("step", skill="skill5", step="assemble", message="正在汇总 manuscript data...")
                _time.sleep(0.3)
                yield emit("step", skill="skill5", step="write", message="正在撰写 IMRAD 报告...")
                orch.run_skill5(sid)
                mlen = len(s.skill5_output) if s.skill5_output else 0
                yield emit("skill_complete", skill="skill5", data={"manuscript": s.skill5_output}, message=f"报告生成完成: {mlen} 字符 — 已就绪")
                yield emit("report_update", skill="skill5", report="manuscript", title="研究报告")
                yield emit("pipeline_complete", message=f"报告生成完成。查看右侧报告面板。")

            yield emit("done")

        except Exception as e:
            import traceback; traceback.print_exc()
            yield emit("error", message=str(e))
            yield emit("done")

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        }
    )


# ---- Data info ----

@app.route("/api/data-info")
def api_data_info():
    """Get basic info about the available data."""
    data_path = Path(DATA_DIR)
    if not data_path.exists():
        return jsonify({"error": "Data directory not found"})

    files = []
    for f in sorted(data_path.glob("*.csv")):
        files.append({
            "name": f.name,
            "size_mb": round(f.stat().st_size / (1024 * 1024), 2),
        })

    return jsonify({
        "data_directory": str(data_path),
        "files": files,
        "file_count": len(files),
    })


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("MIMIC Research Agent — Clinical Research Workflow Platform")
    print("=" * 60)
    print(f"Data directory: {DATA_DIR}")
    print(f"Skills directory: /Users/tomo/Desktop/AI/SKILL1234")
    print()
    port = int(os.environ.get("PORT", 8080))
    print(f"Starting web server at http://localhost:{port}")
    print("=" * 60)

    app.run(debug=True, host="0.0.0.0", port=port)
