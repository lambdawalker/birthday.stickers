"""OpenAI-compatible scene planning and local refinement-request composition."""

import base64
import io
import json
import re
from pathlib import Path

from .config import LLMConfig, auth_headers
from .http import HTTPClient, ServiceError
from .images import decode_image, rgb_image


def compose_refinement(template: str, guide: str, scene: str) -> str:
    if "{{guide}}" not in template or "{{scene}}" not in template:
        raise ValueError("Refinement template must contain {{guide}} and {{scene}}")
    # Single-pass substitution: braces in an LLM response or guide are literal data.
    values = {"guide": guide, "scene": scene}
    return re.sub(r"\{\{(guide|scene)\}\}", lambda match: values[match[1]], template)


def response_text(response: dict, refined: bool) -> str:
    try:
        choice = response["choices"][0]
        message = choice["message"]
        if choice.get("finish_reason") in ("length", "content_filter", "tool_calls"):
            raise ServiceError(
                f"LLM did not finish: {choice['finish_reason']}; check token limit/model"
            )
        if message.get("refusal"):
            raise ServiceError(f"LLM refused the request: {message['refusal']}")
        content = message["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise ServiceError("LLM response is missing choices[0].message.content") from error
    if isinstance(content, list):
        content = "\n".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    if not isinstance(content, str) or not content.strip():
        raise ServiceError("LLM returned an empty scene")
    content = content.strip()
    if "<think>" in content or "</think>" in content:
        raise ServiceError(
            "LLM put reasoning in the scene; disable thinking or use a compatible parser"
        )
    if refined:
        # Accommodate the official T2I JSON envelope, but send ONLY text to Qwen.
        candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", content)
        if candidate.startswith("{"):
            try:
                value = json.loads(candidate)["rewritten_prompt"]
            except (ValueError, KeyError, TypeError) as error:
                raise ServiceError("Refinement JSON must contain rewritten_prompt") from error
            if not isinstance(value, str) or not value.strip():
                raise ServiceError("LLM returned an empty rewritten_prompt")
            content = value
        elif content.startswith("```"):
            raise ServiceError("LLM returned a code block instead of a plain editing prompt")
        content = " ".join(content.split())
    return content


class LLMClient:
    def __init__(
        self, config: LLMConfig, references: tuple[Path, ...], generation_type="viking_duo"
    ):
        self.generation_type = generation_type
        self.config = config
        self.http = HTTPClient(config.base_url, config.timeout, auth_headers(config.api_key_env))
        self.reference_parts = []
        if config.send_reference_images:
            for path in references:
                image = rgb_image(decode_image(path))
                image.thumbnail((1536, 1536))
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=90)
                encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
                self.reference_parts.append(
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}}
                )

    def complete(self, prompt: str, *, refined: bool = False) -> str:
        role_context = (
            "The attached images are ordered: protagonist as <image1>, creature as <image2>. "
            if self.reference_parts
            else "You cannot see the character images. Refer to their identities by role; "
            "do not invent details of their appearance. Qwen will receive the actual images. "
        )
        system = role_context + (
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
        if self.generation_type == "baby_dragon":
            system = (
                "Describe one baby dragon as the only character, following the local brief. "
                "This is text-to-image generation with no reference images. "
                "Return only one English visual scene paragraph, without image tags. "
                "Preserve the selected activity, character description, palette and style. "
                "Output dimensions are controlled by the application."
            )
        user_content = (
            [{"type": "text", "text": prompt}, *self.reference_parts]
            if self.reference_parts
            else prompt
        )
        payload = {
            **self.config.extra_body,
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            "temperature": (
                self.config.refinement_temperature if refined else self.config.temperature
            ),
            self.config.token_limit_field: self.config.max_tokens,
            "stream": False,
        }
        return response_text(self.http.json("POST", "/chat/completions", payload), refined)
