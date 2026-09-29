"""Shared artwork requirements for conversational and request-based generators."""

from __future__ import annotations


INTRODUCTION = (
    "This conversation is for creating artwork for a desktop icon-making app. "
    "I’ll describe the image I want, and you’ll generate the artwork."
)

DEFAULTS = """Unless I explicitly request otherwise, follow these defaults:

- Create one finished icon composition on a square canvas.
- Use a genuinely transparent background with clean edges. Do not draw a checkerboard or another pattern to imitate transparency.
- Keep the complete subject visible, including any intended glow or shadow. Use the canvas efficiently without clipping the artwork or leaving excessive empty space.
- Give the artwork a clear, recognizable silhouette while preserving fine detail for larger views.
- Follow the style, colors, materials, and subject specified in each request.
- Do not add scenery, a background panel, a decorative frame, or text unless requested.
- Provide a full-resolution PNG where supported. The app will handle conversion into Windows ICO files.
- When revising an image, preserve the elements I have not asked you to change.
- Treat a new subject as a new design unless I ask you to continue a matching set."""

OVERRIDES = """These are defaults. My specific instructions for an image can override them. If a requested output feature is unavailable, explain that briefly rather than pretending it was provided."""

VISUAL_REQUIREMENTS = f"{DEFAULTS}\n\n{OVERRIDES}"
SHARED_REQUIREMENTS = f"{INTRODUCTION}\n\n{VISUAL_REQUIREMENTS}"

OPENING_BRIEF = (
    SHARED_REQUIREMENTS
    + "\n\nThis message supplies setup instructions only. Wait for my first image description before generating anything."
)


def codex_generation_request(description: str) -> str:
    return (
        SHARED_REQUIREMENTS
        + "\n\nUse the configured image-generation tool to create the requested artwork. "
        "Apply the requirements above to the following image request:\n\n"
        + description.strip()
    )


def visual_generation_request(description: str) -> str:
    """Add the defaults to a Designer request when that site is submitted."""
    return f"{VISUAL_REQUIREMENTS}\n\nImage request:\n{description.strip()}"
