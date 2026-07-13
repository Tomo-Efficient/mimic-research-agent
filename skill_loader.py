"""
Skill Document Loader — reads SKILL.md files and references into structured context.
Each skill's full documentation becomes the authoritative instruction set for execution.
"""

import os
from pathlib import Path
from dataclasses import dataclass, field

SKILLS_DIR = Path(os.environ.get("SKILLS_DIR", str(Path(__file__).parent / "skills")))

SKILL_MAP = {
    "skill1": "mimic-eda-skills",
    "skill2": "medical-literature-retrieval-workflow",
    "skill3": "mimic-cohort-query",
    "skill4": "medical-stats-test",
    "skill5": "mimic-report-generation",
}


@dataclass
class SkillContext:
    """Holds the full context for a skill: main doc + all references."""
    name: str
    folder: str
    skill_md: str = ""
    references: dict[str, str] = field(default_factory=dict)
    scripts: dict[str, str] = field(default_factory=dict)

    @property
    def full_context(self) -> str:
        """Assemble the complete skill context for LLM prompting."""
        parts = [self.skill_md]
        for ref_name, ref_content in self.references.items():
            parts.append(f"\n---\n## Reference: {ref_name}\n{ref_content}")
        for script_name, script_content in self.scripts.items():
            parts.append(f"\n---\n## Script: {script_name}\n```python\n{script_content}\n```")
        return "\n".join(parts)


def load_skill(skill_name: str) -> SkillContext:
    """Load a skill by name (skill1, skill2, skill3, skill4, skill5)."""
    folder = SKILL_MAP.get(skill_name)
    if not folder:
        raise ValueError(f"Unknown skill: {skill_name}")

    ctx = SkillContext(name=skill_name, folder=folder)

    # Load SKILL.md
    skill_md_path = SKILLS_DIR / folder / "SKILL.md"
    if skill_md_path.exists():
        ctx.skill_md = skill_md_path.read_text(encoding="utf-8")

    # Load references/
    refs_dir = SKILLS_DIR / folder / "references"
    if refs_dir.exists():
        for ref_file in sorted(refs_dir.glob("*.md")):
            ctx.references[ref_file.name] = ref_file.read_text(encoding="utf-8")

    # Load scripts/
    scripts_dir = SKILLS_DIR / folder / "scripts"
    if scripts_dir.exists():
        for script_file in sorted(scripts_dir.glob("*.py")):
            ctx.scripts[script_file.name] = script_file.read_text(encoding="utf-8")

    return ctx


def load_all_skills() -> dict[str, SkillContext]:
    """Load all five skills."""
    return {name: load_skill(name) for name in SKILL_MAP}
