"""
Agent Orchestrator — manages the full 5-skill workflow.
Maintains session state and coordinates skill execution.
"""

import json
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Any

from skills import Skill1EDA, SkillCohort, Skill4Stats
from llm_utils import generate_ideas, generate_report, search_papers, generate_reproduction_report


@dataclass
class SessionState:
    """Holds all intermediate results for a user session."""
    data_dir: str = ""
    skill1_output: dict | None = None
    skill2_mode: str = ""
    skill2_output: dict | None = None
    selected_idea_id: str = ""
    selected_idea: dict | None = None
    skill3_output: dict | None = None
    skill4_output: dict | None = None
    skill5_output: str = ""
    current_page: int = 1
    ideas_batch_offset: int = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        # Truncate large fields for session storage
        if d.get("skill1_output"):
            d["skill1_output"] = self._truncate_skill1(d["skill1_output"])
        return d

    def _truncate_skill1(self, output: dict) -> dict:
        """Keep only essential info from skill1 to avoid huge sessions."""
        if not output:
            return output
        return {
            "skill": output.get("skill"),
            "status": output.get("status"),
            "data_directory": output.get("data_directory"),
            "tables_found": output.get("tables_found"),
            "table_names": output.get("table_names", [])[:10],
            "table_sizes": output.get("table_sizes", {}),
            "total_patients_estimate": output.get("total_patients_estimate"),
            "relationships": output.get("relationships", [])[:10],
            "summary": output.get("summary", {}),
        }


class AgentOrchestrator:
    """Orchestrates the 5-skill clinical research workflow."""

    def __init__(self):
        self.sessions: dict[str, SessionState] = {}

    def get_session(self, session_id: str) -> SessionState:
        if session_id not in self.sessions:
            self.sessions[session_id] = SessionState()
        return self.sessions[session_id]

    # ---- Skill 1: EDA ----
    def run_skill1(self, session_id: str, data_dir: str) -> dict:
        """Run EDA on the MIMIC data directory."""
        session = self.get_session(session_id)
        session.data_dir = data_dir
        session.current_page = 1

        skill1 = Skill1EDA(data_dir)
        output = skill1.run()
        session.skill1_output = output
        return output

    # ---- Skill 2: Idea Generation ----
    def run_skill2(self, session_id: str, mode: str) -> dict:
        """Route to the correct Skill 2 handler based on mode."""
        session = self.get_session(session_id)
        session.skill2_mode = mode
        session.current_page = 2

        if not session.skill1_output:
            return {"error": "Skill 1 must be run first"}

        eda = session.skill1_output

        if mode == "paper_reproduction":
            # Search for real published papers to reproduce
            output = search_papers(eda)
        elif mode == "ai_assisted":
            # Agent generates novel research ideas
            output = generate_ideas(eda, mode=mode)
        elif mode == "independent_exploration":
            # User will submit their own idea via /api/skill2/submit-idea
            output = {
                "skill": "skill2_user",
                "status": "awaiting_input",
                "mode": "independent_exploration",
                "message": "Please submit your research idea using the form."
            }
        else:
            return {"error": f"Unknown mode: {mode}"}

        session.skill2_output = output
        return output

    def set_user_idea(self, session_id: str, idea: dict):
        """Set a user-submitted idea for independent exploration mode."""
        session = self.get_session(session_id)
        session.skill2_mode = "independent_exploration"
        session.selected_idea_id = idea["id"]
        session.selected_idea = idea
        session.skill2_output = {
            "skill": "skill2_user",
            "status": "completed",
            "mode": "independent_exploration",
            "user_idea": idea
        }
        session.current_page = 2

    def select_idea(self, session_id: str, idea_id: str) -> dict:
        """Select a specific research idea or paper from session or ideas pool."""
        session = self.get_session(session_id)
        session.selected_idea_id = idea_id

        # Search in session skill2 output first
        if session.skill2_output:
            ideas = session.skill2_output.get("ideas", [])
            for idea in ideas:
                if idea.get("id") == idea_id:
                    session.selected_idea = idea
                    return {"status": "selected", "idea": idea}

            papers = session.skill2_output.get("papers", [])
            for paper in papers:
                if paper.get("id") == idea_id:
                    idea = {
                        "id": paper.get("id", ""),
                        "title": paper.get("title", ""),
                        "research_question": f"Reproduce: {paper.get('title', '')}",
                        "hypothesis": paper.get("key_findings", ""),
                        "pico": {
                            "P": paper.get("population", ""),
                            "I": paper.get("exposure", ""),
                            "C": "Control group",
                            "O": paper.get("outcome", ""),
                        },
                        "data_variables": paper.get("data_variables", []),
                        "suggested_method": paper.get("methods", ""),
                        "target_paper_type": "reproduction",
                        "reproduction_plan": paper.get("reproduction_plan", ""),
                        "scores": {},
                        "total_score": 0,
                        "rationale": f"Reproducing: {paper.get('title', '')} ({paper.get('journal', '')}, {paper.get('year', '')})",
                        "source": "paper_reproduction",
                        "paper_ref": paper,
                    }
                    session.selected_idea = idea
                    return {"status": "selected", "idea": idea}

        # Fallback: search in the cached ideas pool (DB/Redis)
        from db import get_ideas_pool
        pool = get_ideas_pool()
        for idea in pool:
            if idea.get("id") == idea_id:
                session.selected_idea = idea
                return {"status": "selected", "idea": idea}

        return {"error": f"Idea {idea_id} not found"}

    def shuffle_ideas(self, session_id: str) -> dict:
        """Cycle to the next batch of 10 ideas from the pool."""
        from db import get_ideas_pool
        session = self.get_session(session_id)
        pool = get_ideas_pool()
        if not pool:
            return {"error": "No ideas pool available"}
        pool_sorted = sorted(pool, key=lambda x: x.get("total_score", 0), reverse=True)
        batch_size = 10
        total = len(pool_sorted)
        # Cycle to next batch
        new_offset = (session.ideas_batch_offset + batch_size) % total
        session.ideas_batch_offset = new_offset
        batch = pool_sorted[new_offset:new_offset + batch_size]
        session.skill2_output = {
            "skill": "skill2_ideas", "status": "completed", "mode": "ai_assisted",
            "ideas": batch, "total_ideas": total,
            "batch_offset": new_offset, "batch_size": batch_size,
        }
        return session.skill2_output

    # ---- Skill 3: Cohort Extraction ----
    def run_skill3(self, session_id: str) -> dict:
        """Extract the patient cohort and analysis variables for the selected idea."""
        session = self.get_session(session_id)

        if not session.selected_idea:
            return {"error": "No idea selected"}

        cohort = SkillCohort(session.data_dir)
        eda = session.skill1_output or {}

        output = cohort.run(session.selected_idea, eda)
        session.skill3_output = output
        session.current_page = 3
        return output

    # ---- Skill 4: Statistical Testing ----
    def run_skill4(self, session_id: str) -> dict:
        """Run statistical tests for the selected idea."""
        session = self.get_session(session_id)

        if not session.selected_idea:
            return {"error": "No idea selected"}

        skill4 = Skill4Stats(session.data_dir)
        eda = session.skill1_output or {}

        output = skill4.run(session.selected_idea, eda)
        session.skill4_output = output
        session.current_page = 4
        return output

    # ---- Skill 5: Report Generation ----
    def run_skill5(self, session_id: str) -> str:
        """Generate the final output based on mode:
        - paper_reproduction: reproduction comparison report
        - ai_assisted / independent_exploration: IMRAD paper
        """
        session = self.get_session(session_id)

        if not session.skill4_output:
            return "Error: Skill 4 results not available"

        mode = session.skill2_mode

        if mode == "paper_reproduction":
            manuscript = generate_reproduction_report(
                eda_report=session.skill1_output or {},
                selected_idea=session.selected_idea or {},
                stats_results=session.skill4_output,
            )
        else:
            manuscript = generate_report(
                eda_report=session.skill1_output or {},
                ideas=session.skill2_output or {},
                selected_idea=session.selected_idea or {},
                stats_results=session.skill4_output,
            )

        session.skill5_output = manuscript
        session.current_page = 5
        return manuscript

    def reset_session(self, session_id: str):
        if session_id in self.sessions:
            del self.sessions[session_id]

    def get_progress(self, session_id: str) -> dict:
        """Get current workflow progress."""
        session = self.get_session(session_id)
        return {
            "current_page": session.current_page,
            "skill1_done": session.skill1_output is not None,
            "skill2_done": session.skill2_output is not None,
            "idea_selected": session.selected_idea is not None,
            "skill3_done": session.skill3_output is not None,
            "skill4_done": session.skill4_output is not None,
            "skill5_done": bool(session.skill5_output),
            "selected_idea_id": session.selected_idea_id,
            "mode": session.skill2_mode,
        }


# Global orchestrator instance
orchestrator = AgentOrchestrator()
