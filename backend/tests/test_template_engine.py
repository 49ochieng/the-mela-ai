"""Tests for the template_service: parse + render_prompt_block."""

import pytest

from app.services.template_service import template_service


def test_parse_markdown_extracts_headings_and_placeholders():
    md = """# Quarterly Brief

## Executive Summary
Provide a concise overview for {{audience}}.

## Key Metrics
- Revenue: {{revenue}}
- Growth: [FILL]

## Risks
<<risks_section>>
"""
    schema = template_service.parse(text=md, file_type="md", raw_bytes=md.encode("utf-8"))
    headings = [s["heading"] for s in schema["sections"]]
    assert "Quarterly Brief" in headings
    assert "Executive Summary" in headings
    assert "Key Metrics" in headings
    assert "Risks" in headings

    # placeholders rolled up across sections
    all_ph = {p for s in schema["sections"] for p in s["placeholders"]}
    assert "audience" in all_ph
    assert "revenue" in all_ph
    assert "risks_section" in all_ph
    assert "FILL" in all_ph


def test_parse_returns_branding_title_from_first_heading():
    md = "# Acme Brand Guide\n\n## Section\nbody\n"
    schema = template_service.parse(text=md, file_type="md", raw_bytes=md.encode("utf-8"))
    assert schema["branding"]["title"] == "Acme Brand Guide"


def test_render_prompt_block_includes_sections_and_placeholders():
    md = "# Report\n\n## Intro\nHello {{name}}\n"
    schema = template_service.parse(text=md, file_type="md", raw_bytes=md.encode("utf-8"))
    block = template_service.render_prompt_block(schema, max_chars=2000)
    assert "Intro" in block
    assert "name" in block
    assert len(block) <= 2000


def test_render_prompt_block_truncates_at_max_chars():
    md = "# Big\n\n" + "\n\n".join(
        f"## Section {i}\n{{{{slot_{i}}}}}" for i in range(200)
    )
    schema = template_service.parse(text=md, file_type="md", raw_bytes=md.encode("utf-8"))
    block = template_service.render_prompt_block(schema, max_chars=300)
    assert len(block) <= 300


def test_unsupported_file_type_returns_minimal_schema():
    schema = template_service.parse(text="plain text body", file_type="txt", raw_bytes=b"plain text body")
    # Should still return the dict shape without crashing
    assert "sections" in schema
    assert "branding" in schema
    assert "tone_summary" in schema
