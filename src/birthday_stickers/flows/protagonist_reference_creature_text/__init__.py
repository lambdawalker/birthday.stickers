"""A referenced protagonist and a text-described companion in a shared scene."""

import copy

from ...retry import file_retry
from ...workflows import apply_overrides, core_saver, mapped_inputs, qwen_negative, reachable_graph

USES_REFERENCES = True
REFERENCE_ROLES = ("protagonist",)


@file_retry
def build_graph(config, template, prompt, references, seed, prefix):
    graph = copy.deepcopy(template)
    apply_overrides(graph, config.qwen.overrides)
    nodes = config.qwen.nodes
    mapped_inputs(graph, nodes, "protagonist", "LoadImage")["image"] = references[0]
    encoder = mapped_inputs(graph, nodes, "encoder", "TextEncodeQwenImage21")
    encoder["prompt"] = prompt
    encoder["images.image_1"] = [str(nodes["protagonist"]), 0]
    encoder.pop("images.image_2", None)
    mapped_inputs(graph, nodes, "latent", "EmptyLatentImage").update(
        width=config.generation.width, height=config.generation.height, batch_size=1
    )
    sampler = mapped_inputs(graph, nodes, "sampler", "KSampler")
    sampler.update(seed=seed, latent_image=[str(nodes["latent"]), 0])
    if config.generation.cfg is not None:
        sampler["cfg"] = config.generation.cfg
    qwen_negative(
        graph,
        nodes,
        config.negative.read_text(encoding="utf-8").strip() if config.negative else None,
    )
    core_saver(graph, nodes, prefix)
    return reachable_graph(graph, str(nodes["output"]))


def system_prompt(refined, has_images):
    return (
        "The only reference, <image1>, defines the baby girl's identity and outfit. "
        + (
            "You can see this image. "
            if has_images
            else "You cannot see it; do not claim to inspect it. "
        )
        + "The baby dragon has no reference: describe its black body, green eyes, pointed ears, "
        "no horns, four legs, two wings, and tail in words. Exactly two characters. "
        + (
            "Rewrite as one Qwen instruction paragraph with <image1> and no other image tags. "
            if refined
            else "Plan one English scene: shared activity, Viking setting, and pastel scenery. "
        )
        + "Center both full bodies in continuous borderless scenery. Return only scene text."
    )
