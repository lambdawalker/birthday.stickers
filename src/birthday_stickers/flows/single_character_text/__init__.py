"""One text-described character, without identity-reference images."""

from ...retry import file_retry
from ...workflows import build_text_qwen

USES_REFERENCES = False


@file_retry
def build_graph(config, template, prompt, references, seed, prefix):
    return build_text_qwen(
        template,
        config.qwen.nodes,
        config.qwen.overrides,
        prompt,
        config.generation.width,
        config.generation.height,
        seed,
        prefix,
        negative=config.negative.read_text(encoding="utf-8").strip() if config.negative else None,
    )


def system_prompt(refined, has_images):
    return (
        "Describe one character only, following the local brief. "
        "This is text-to-image generation with no reference images. "
        "Return only one English visual scene paragraph, without image tags. "
        "Preserve the selected activity, character description, palette and style. "
        "Output dimensions are controlled by the application."
    )
