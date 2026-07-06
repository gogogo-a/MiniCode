"""
Skill 模块：扫描 skills 目录，并给 agent 提供技能目录和技能全文。

函数职责：
- parse_frontmatter：解析 SKILL.md 或 memory 文件顶部的 YAML-like 元数据。
- scan_skills：读取 skills/*/SKILL.md，建立内存中的技能注册表。
- list_skills：返回 system prompt 里展示的技能索引。
- load_skill：按名称返回某个技能的完整说明，供 load_skill 工具调用。
"""

from config import SKILLS_DIR


SKILL_REGISTRY: dict[str, dict] = {}


def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta = {}
    for line in parts[1].splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip().strip('"')
    return meta, parts[2].strip()


def scan_skills():
    if not SKILLS_DIR.exists():
        return
    for directory in sorted(SKILLS_DIR.iterdir()):
        if not directory.is_dir():
            continue
        manifest = directory / "SKILL.md"
        if not manifest.exists():
            continue
        raw = manifest.read_text(encoding="utf-8")
        meta, _ = parse_frontmatter(raw)
        name = meta.get("name", directory.name)
        description = meta.get("description", raw.splitlines()[0].lstrip("#").strip())
        SKILL_REGISTRY[name] = {"name": name, "description": description, "content": raw}


def list_skills() -> str:
    if not SKILL_REGISTRY:
        return "(no skills found)"
    return "\n".join(f"- {skill['name']}: {skill['description']}" for skill in SKILL_REGISTRY.values())


def load_skill(name: str) -> str:
    skill = SKILL_REGISTRY.get(name)
    if not skill:
        return f"Skill not found: {name}"
    return skill["content"]


scan_skills()
