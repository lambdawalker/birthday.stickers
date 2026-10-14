Rewrite the scene below into an actionable multi-reference instruction for
Qwen-Image-2.1. Apply the editing and identity-reference sections of this guide;
its unrelated recipes and code examples are reference material, not new requests.

<qwen_guide>
{{guide}}
</qwen_guide>

<original_scene>
{{scene}}
</original_scene>

Task-specific contract (takes precedence over generic examples in the guide):

- This is NEW SCENE generation from two identity references, not a local edit to
  either reference's background. Begin with the operation creating that scene.
- Name <image1> individually as the protagonist identity source. Preserve that
  character's identity, proportions, hairstyle, recognizable clothing, and design.
- Name <image2> individually as the fantastic creature identity source. Preserve
  its recognizable face, silhouette, anatomy, colors, and design.
- Neither image is a base canvas. Let pose, viewpoint, interaction, and environment
  follow the original scene. Do not freeze poses/backgrounds from the references.
- Keep the original scene's subject count, action, environment, style, placement,
  lighting, and any literal lettering. Use source-image references instead of
  inventing facial traits. Add no extra characters, captions, or accessories.
- Preserve the original scene's Viking-inspired setting, shared activity, and
  explicit pastel background colors. Keep the girl's and dragon's reference colors
  intact; do not apply the background palette to their clothing, skin, or body.
- Keep both subjects fully inside the central composition. Describe coherent
  surroundings that can later continue past all edges. Do not ask Qwen to perform
  the later outpainting operation in this first render.
- If you cannot see the references, do not claim to inspect their appearance.
- Output one continuous paragraph of plain English instructions with both image
  tags, and nothing else. No JSON envelope, headings, commentary, or code fences.
  Quote only text actually meant to be printed in the image; preserve its language.
  Keep aspect ratio and resolution out of the prose: YAML controls dimensions.

The composition is a complete, borderless 2D illustration. Continuous scenery fills the canvas edge to edge; no frame, border, inset panel, blank margin, or 3D-rendered appearance. Include this edge-to-edge composition in the scene description.
