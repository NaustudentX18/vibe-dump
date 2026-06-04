"""Shared schema constants for Vibe-Dump."""

BLUEPRINT_SECTIONS = [
    "Project Snapshot",
    "User / Audience",
    "Core Problem",
    "Product Vision",
    "Core Architecture",
    "Data Flow",
    "Tech Stack",
    "Features",
    "Step-by-Step Build Tasks",
    "Agent Handoff Prompts",
    "Risks / Unknowns",
    "RAG Memory Overlaps",
]


def blueprint_template(title: str = "Untitled Dump") -> str:
    """Return a copy-ready markdown blueprint skeleton."""
    lines = ["# Vibe Coding Blueprint", "", f"<!-- Source dump: {title} -->", ""]
    for idx, section in enumerate(BLUEPRINT_SECTIONS, start=1):
        lines.extend([f"## {idx}. {section}", "", "TBD", ""])
    return "\n".join(lines).strip() + "\n"


def validate_blueprint(markdown: str) -> list[str]:
    """Return missing required section names for a blueprint markdown document."""
    missing: list[str] = []
    for idx, section in enumerate(BLUEPRINT_SECTIONS, start=1):
        if f"## {idx}. {section}" not in markdown:
            missing.append(section)
    return missing
