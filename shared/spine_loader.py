"""
Spine Loader: Sectional parsing for agent identity documents (SELF.md).

Supports extracting sections starting with '# <AGENT_NAME>' headers.
Falls back to '# DEFAULT' if no match is found.
"""

import re
from pathlib import Path
from typing import Optional
from shared.config_loader import CONFIG_DIR

SELF_MD_PATH = CONFIG_DIR / "SELF.md"

def load_spine(agent_name: str) -> str:
    """Load and parse the SELF.md document for a specific agent."""
    if not SELF_MD_PATH.exists():
        return ""
    
    try:
        content = SELF_MD_PATH.read_text(errors="replace")
        return parse_spine(content, agent_name)
    except Exception as e:
        print(f"⚠️ [SpineLoader] Error loading SELF.md: {e}")
        return ""

def parse_spine(content: str, agent_name: str) -> str:
    """Parse sectional content from a markdown string.
    
    1. Look for '# <AGENT_NAME>'
    2. Fallback to '# DEFAULT'
    3. Final fallback: whole file if no headers exist.
    """
    # Regex to find top-level headers (beginning of line, # followed by name)
    # capturing everything until the next top-level header or total EOF
    def extract_section(tag: str) -> Optional[str]:
        pattern = rf"^#\s+{re.escape(tag)}\s*\n(.*?)(?=^#\s|\Z)"
        match = re.search(pattern, content, re.MULTILINE | re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return None

    # Step 1: Specific Agent Section
    agent_section = extract_section(agent_name)
    if agent_section is not None:
        return agent_section

    # Step 2: DEFAULT Section
    default_section = extract_section("DEFAULT")
    if default_section is not None:
        return default_section

    # Step 3: No headers at all? (Return whole file as fallback)
    if not re.search(r"(?mi)^#\s+", content):
        return content.strip()

    return ""
