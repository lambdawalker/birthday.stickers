"""Two ordered identity references plus a locally planned scene."""

from ...retry import file_retry
from ...workflows import build_qwen

USES_REFERENCES = True


@file_retry
def build_graph(config, template, prompt, references, seed, prefix):
    overrides = {key: dict(value) for key, value in config.qwen.overrides.items()}
    if config.generation.cfg is not None:
        overrides.setdefault(str(config.qwen.nodes["sampler"]), {})["cfg"] = config.generation.cfg
    return build_qwen(
        template,
        config.qwen.nodes,
        overrides,
        prompt,
        *references,
        config.generation.width,
        config.generation.height,
        seed,
        prefix,
        negative=config.negative.read_text(encoding="utf-8").strip() if config.negative else None,
    )


def system_prompt(refined, has_images):
    context = (
        "The attached images are ordered: protagonist as <image1>, creature as <image2>. "
        if has_images
        else "You cannot see the character images. Refer to their identities by role; "
        "do not invent details of their appearance. Qwen will receive the actual images. "
    )
    return context + (
        "Rewrite the supplied scene as a multi-reference Qwen editing instruction. "
        "Both images are identity sources for a NEW scene; neither is a base canvas. "
        "Preserve identity while allowing the scene's pose and environment to change. "
        "Return only one plain-text paragraph, with <image1> and <image2> roles explicit. "
        "Output dimensions are controlled by the application."
        if refined
        else "Plan one complete visual scene following the local brief. Return only the scene "
        "description in English. This original scene will also be used for outpainting. "
        "Describe the environment and how it continues beyond the central characters. "
        "Preserve any requested visible text exactly in its original language."
    )
