"""Single-pass master templates and explicit Qwen character-tag contracts."""

import re

from jinja2 import StrictUndefined, TemplateError, meta
from jinja2.sandbox import SandboxedEnvironment


def render_master(template, context, required, name):
    env = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False)
    env.globals.clear()
    try:
        variables = meta.find_undeclared_variables(env.parse(template))
        missing = set(required) - variables
        if missing:
            raise ValueError("required placeholders: " + ", ".join(sorted(missing)))
        return env.from_string(template).render(context).strip(), variables
    except (TemplateError, ValueError) as error:
        raise ValueError(f"{name}: {error}") from error


def plan_request(template, brief, previous, iteration, count, sticker):
    return render_master(
        template,
        {
            "scene_brief": brief,
            "previous_scenes": "\n".join(previous[-10:]),
            "iteration": iteration,
            "count": count,
            "sticker_id": sticker,
        },
        ("scene_brief",),
        "Planning master",
    )[0]


def character_sources(characters):
    lines = []
    for character in characters:
        source = character.get("imageTag") or "text only; no image tag"
        lines.append(
            f"- {character['id']} ({character['name']}): {source}. "
            + character.get("description", "")
        )
    return "\n".join(lines)


def refine_request(template, guide, scene, characters):
    sources = character_sources(characters)
    result, _ = render_master(
        template,
        {
            "guide": guide,
            "scene": scene,
            "character_sources": sources,
        },
        ("guide", "scene", "character_sources"),
        "Refinement master",
    )
    return result


def tag_error(prompt, required):
    actual = set(re.findall(r"<image\d+>", prompt))
    missing = set(required) - actual
    extra = actual - set(required)
    errors = []
    if missing:
        errors.append("Missing required image tags: " + ", ".join(sorted(missing)))
    if extra:
        errors.append("Unexpected image tags: " + ", ".join(sorted(extra)))
    return ". ".join(errors)


def correction_request(base, candidate, error):
    return (
        base
        + "\n\nYour previous answer did not follow the character-source assignments.\n"
        + error
        + ".\nPrevious answer:\n"
        + candidate
        + "\n\nReturn the corrected complete scene paragraph. Put each required literal "
        "<imageN> tag beside its assigned character. Text-only characters must have no "
        "image tag. Keep the same scene and identities; do not add a tag list or commentary."
    )
