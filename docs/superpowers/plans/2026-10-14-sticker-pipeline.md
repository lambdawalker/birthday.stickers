# Implementation plan

Spec: ../specs/2026-10-14-sticker-pipeline-design.md

1. Add configuration and graph/image tests, observe failures, then implement
   validated YAML, workflow patching, padding, image conversion, and graph pruning.
   All paths are relative to config; originals remain unchanged.
2. Add a local HTTP integration fixture and failing pipeline tests. Implement the
   HTTP transport, LLM planner/refiner, ComfyUI client, output journal, and CLI.
   GET retries are bounded; POST submissions are never retried; errors keep files.
3. Add the editable planning/refining prompts, preserve and supplement the guide,
   provide Windows-friendly uv setup instructions and CI.
4. Run all unittest tests, lint, package build, CLI help and local validation.
   Review the whole branch, address functional findings, publish a feature branch
   and pull request. Real GPU/model quality validation remains for configured hosts.

## Review focus

- Swapping protagonist/creature or routing refined text into Flux.
- Executing disconnected Qwen enhancement/output branches.
- Odd dimensions, latent alignment, and preserving center without stretching.
- Accidentally resubmitting a ComfyUI job after a network timeout.
- Losing the raw prompt, silently overwriting images, or using another output node.
