"""Patch API-format workflow copies without modifying the exported originals."""

import copy
import json
from pathlib import Path
from typing import Any

from .images import PaddingPlan

Graph = dict[str, dict[str, Any]]


def load_workflow(path: Path) -> Graph:
    graph = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(graph, dict) or not graph or "nodes" in graph:
        raise ValueError(f"{path}: expected ComfyUI API-format JSON (Export API), not UI format")
    for node_id, node in graph.items():
        if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
            raise ValueError(f"{path}: invalid API node {node_id}")
        if not isinstance(node.get("class_type"), str):
            raise ValueError(f"{path}: missing class_type in node {node_id}")
    return graph


def apply_overrides(graph: Graph, overrides: dict) -> None:
    for node_id, inputs in overrides.items():
        node_id = str(node_id)
        if node_id not in graph or not isinstance(inputs, dict):
            raise ValueError(f"Invalid workflow override node {node_id}")
        for name, value in inputs.items():
            if name not in graph[node_id]["inputs"]:
                raise ValueError(f"Unknown input {name!r} for override node {node_id}")
            graph[node_id]["inputs"][name] = value


def mapped_inputs(graph: Graph, mapping: dict, role: str, expected: str | tuple) -> dict:
    node_id = str(mapping.get(role, ""))
    expected = (expected,) if isinstance(expected, str) else expected
    if node_id not in graph or graph[node_id]["class_type"] not in expected:
        raise ValueError(
            f"Workflow role {role!r} must map to {' or '.join(expected)} (got {node_id})"
        )
    return graph[node_id]["inputs"]


def reachable_graph(graph: Graph, output_id: str) -> Graph:
    needed: set[str] = set()
    visiting: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visiting:
            raise ValueError(f"Workflow contains a cycle at node {node_id}")
        if node_id in needed:
            return
        if node_id not in graph:
            raise ValueError(f"Workflow links to missing node {node_id}")
        visiting.add(node_id)
        for value in graph[node_id]["inputs"].values():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[1], int):
                visit(str(value[0]))
        visiting.remove(node_id)
        needed.add(node_id)

    visit(output_id)
    return {key: value for key, value in graph.items() if key in needed}


def core_saver(graph: Graph, mapping: dict, prefix: str) -> None:
    inputs = mapped_inputs(graph, mapping, "output", ("SaveImage", "SaveImageAdvanced"))
    graph[str(mapping["output"])] = {
        "class_type": "SaveImage",
        "inputs": {"images": inputs["images"], "filename_prefix": prefix},
    }


def build_qwen(
    template: Graph,
    nodes: dict,
    overrides: dict,
    prompt: str,
    protagonist: str,
    creature: str,
    width: int,
    height: int,
    seed: int,
    prefix: str,
) -> Graph:
    graph = copy.deepcopy(template)
    apply_overrides(graph, overrides)
    if str(nodes["protagonist"]) == str(nodes["creature"]):
        raise ValueError("Protagonist and creature must have distinct LoadImage nodes")
    mapped_inputs(graph, nodes, "protagonist", "LoadImage")["image"] = protagonist
    mapped_inputs(graph, nodes, "creature", "LoadImage")["image"] = creature
    encoder = mapped_inputs(graph, nodes, "encoder", "TextEncodeQwenImage21")
    encoder.update(
        {
            "prompt": prompt,
            "images.image_1": [str(nodes["protagonist"]), 0],
            "images.image_2": [str(nodes["creature"]), 0],
        }
    )
    mapped_inputs(graph, nodes, "latent", "EmptyLatentImage").update(
        width=width, height=height, batch_size=1
    )
    mapped_inputs(graph, nodes, "sampler", "KSampler").update(
        seed=seed, latent_image=[str(nodes["latent"]), 0]
    )
    core_saver(graph, nodes, prefix)
    return reachable_graph(graph, str(nodes["output"]))


def build_flux(
    template: Graph,
    nodes: dict,
    overrides: dict,
    prompt: str,
    image: str,
    padding: PaddingPlan,
    feathering: int,
    seed: int,
    prefix: str,
) -> Graph:
    graph = copy.deepcopy(template)
    apply_overrides(graph, overrides)
    mapped_inputs(graph, nodes, "image", "LoadImage")["image"] = image
    mapped_inputs(graph, nodes, "prompt", "CLIPTextEncode")["text"] = prompt
    mapped_inputs(graph, nodes, "pad", "ImagePadForOutpaint").update(
        image=[str(nodes["image"]), 0],
        left=padding.left,
        top=padding.top,
        right=padding.right,
        bottom=padding.bottom,
        feathering=feathering,
    )
    mapped_inputs(graph, nodes, "conditioning", "InpaintModelConditioning")["noise_mask"] = True
    mapped_inputs(graph, nodes, "sampler", "KSampler")["seed"] = seed
    core_saver(graph, nodes, prefix)
    return reachable_graph(graph, str(nodes["output"]))
