"""
Mela AI - Title Generation Service

Uses Claude Haiku to generate concise chat titles from the first user message.
Falls back to truncated message if Claude is unavailable.
"""

import logging

from app.core.config import settings

logger = logging.getLogger(__name__)


async def generate_chat_title(first_message: str) -> str:
    """
    Generate a 4-6 word title for a chat based on the first message.

    Uses Claude Haiku for fast, high-quality title generation.
    Falls back to a simple truncation if Claude is unavailable.

    Args:
        first_message: The first user message in the conversation.

    Returns:
        A concise title string (4-6 words).
    """
    # Try Claude Haiku first (fastest Claude model)
    try:
        import anthropic

        if not settings.ANTHROPIC_API_KEY:
            raise ValueError("ANTHROPIC_API_KEY not configured")

        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

        prompt = (
            f'Generate a 4-6 word title for a chat that starts with: '
            f'"{first_message[:500]}". Return only the title, no quotes.'
        )
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=30,
            messages=[{"role": "user", "content": prompt}]
        )

        if response.content and len(response.content) > 0:
            title = response.content[0].text.strip()
            # Remove any surrounding quotes
            title = title.strip('"\'')
            # Limit to reasonable length
            if len(title) > 60:
                title = title[:57] + "..."
            logger.info("Generated chat title using Claude Haiku: %s", title)
            return title

    except ImportError:
        logger.warning("anthropic not installed, falling back")
    except Exception as e:
        logger.warning("Claude title generation failed: %s", e)

    # Try Azure OpenAI as fallback
    try:
        from app.services.openai_service import openai_service

        if openai_service:
            # Use the fast deployment for title generation
            title = await openai_service.generate_title(first_message)
            if title:
                logger.info("Generated title using Azure OpenAI: %s", title)
                return title

    except Exception as e:
        logger.warning("OpenAI title generation failed: %s", e)

    # Final fallback: simple truncation
    logger.info("Using fallback truncated title")
    title = first_message.strip()
    if len(title) > 50:
        # Find a natural break point
        title = title[:50]
        last_space = title.rfind(' ')
        if last_space > 30:
            title = title[:last_space]
        title = title + "..."

    return title
