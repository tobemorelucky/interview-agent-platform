"""Seed experience_keyword_presets with common companies, job roles, and platforms.

Idempotent: skips existing (preset_type, name) pairs.

Usage:
    cd apps/api
    uv run python scripts/seed_experience_keywords.py
"""

import asyncio
import json

from sqlalchemy import select

from interview_api.infrastructure.db.session import async_session_factory
from interview_api.modules.experience.models import ExperienceKeywordPreset

SEEDS = [
    # ── Companies ──
    {"preset_type": "COMPANY", "name": "腾讯", "aliases": ["腾讯", "腾讯云", "微信", "PCG", "CSIG"]},
    {"preset_type": "COMPANY", "name": "字节", "aliases": ["字节", "字节跳动", "抖音", "抖音电商", "TikTok"]},
    {"preset_type": "COMPANY", "name": "阿里", "aliases": ["阿里", "阿里巴巴", "淘天", "阿里云"]},
    {"preset_type": "COMPANY", "name": "美团", "aliases": ["美团", "美团到店", "美团外卖"]},
    {"preset_type": "COMPANY", "name": "百度", "aliases": ["百度", "百度智能云"]},
    # ── Job roles ──
    {"preset_type": "JOB", "name": "后端", "aliases": ["后端", "后端开发", "服务端", "后台", "Java 后端", "Go 后端", "Python 后端"]},
    {"preset_type": "JOB", "name": "Java", "aliases": ["Java", "Java开发", "Java后端", "Spring", "Spring Boot"]},
    {"preset_type": "JOB", "name": "AI应用开发", "aliases": ["AI应用", "大模型应用", "RAG", "Agent", "LLM应用"]},
    {"preset_type": "JOB", "name": "前端", "aliases": ["前端", "Vue", "React", "Web前端"]},
    # ── Platforms ──
    {"preset_type": "PLATFORM", "name": "牛客", "aliases": ["牛客", "nowcoder"]},
    {"preset_type": "PLATFORM", "name": "小红书", "aliases": ["小红书", "xiaohongshu", "xhs"]},
    {"preset_type": "PLATFORM", "name": "抖音", "aliases": ["抖音", "douyin", "TikTok"]},
    {"preset_type": "PLATFORM", "name": "全网", "aliases": ["web", "all", "全网公开网页"]},
]


async def main():
    async with async_session_factory() as db:
        created = 0
        skipped = 0
        for item in SEEDS:
            existing = await db.execute(
                select(ExperienceKeywordPreset).where(
                    ExperienceKeywordPreset.preset_type == item["preset_type"],
                    ExperienceKeywordPreset.name == item["name"],
                )
            )
            if existing.scalar_one_or_none():
                skipped += 1
                continue
            preset = ExperienceKeywordPreset(
                preset_type=item["preset_type"],
                name=item["name"],
                aliases_json=item["aliases"],
                enabled=True,
            )
            db.add(preset)
            created += 1
        await db.commit()
        print(f"Seed complete: created={created}, skipped={skipped}")


if __name__ == "__main__":
    asyncio.run(main())
