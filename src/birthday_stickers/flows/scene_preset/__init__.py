"""One Qwen graph for arbitrary text/reference character combinations."""

import copy

from ...retry import file_retry
from ...workflows import apply_overrides, core_saver, mapped_inputs, qwen_negative, reachable_graph

USES_REFERENCES = False
REFERENCE_ROLES = ()


@file_retry
def build_graph(config, template, prompt, references, seed, prefix):
    if len(references) > 10:
        raise ValueError("Qwen supports at most 10 reference images")
    graph = copy.deepcopy(template)
    apply_overrides(graph, config.qwen.overrides)
    nodes = config.qwen.nodes
    encoder = mapped_inputs(graph, nodes, "encoder", "TextEncodeQwenImage21")
    for key in list(encoder):
        if key.startswith("images.image_"):
            del encoder[key]
    encoder["prompt"] = prompt
    for index, path in enumerate(references, 1):
        node = f"character_reference_{index}"
        if node in graph:
            raise ValueError(f"Reserved workflow node ID: {node}")
        graph[node] = {"class_type": "LoadImage", "inputs": {"image": path}}
        encoder[f"images.image_{index}"] = [node, 0]
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
        "Follow the supplied scene preset and character source assignments exactly. "
        "Only characters explicitly assigned an image tag have a reference. "
        + (
            "Attached images follow those tags in numeric order. "
            if has_images
            else "You cannot see reference images; do not claim to inspect them. "
        )
        + (
            "Rewrite one scene as a plain Qwen instruction paragraph "
            "using exactly the assigned image tags. "
            if refined
            else "Plan one complete visual scene in English. Respect the supplied style, "
            "characters, activity, venue and props. "
        )
        + "Descriptions supplement reference identity; "
        "text-only characters must be described explicitly. "
        "Keep the requested character count and return only the scene description."
    )
