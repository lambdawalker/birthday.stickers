# Character references

Add your own images here before running:

- `A000.jpg`, `A001.jpg`, …: references for character A (default protagonist).
- `B000.jpg`, `B001.jpg`, …: references for character B (default fantastic creature).

The format is `[character ID A-Z][numeric reference number].jpg`. The number can
have any number of digits, and `.JPG` is accepted too. Put the files directly in
this directory; the program ignores unrelated filenames and subdirectories.

Configure `characters.directory`, `characters.protagonist: A`, and
`characters.creature: B` in the YAML. IDs may be any two distinct uppercase letters.
Paths resolve relative to the YAML. A new random pair is selected for each scene,
with repeats allowed; the protagonist always occupies Qwen socket 1 and the
creature socket 2. Actual selections are saved in each sticker's metadata.

Legacy configurations with explicit image paths and no `directory` still work.
No reference images were supplied with this repository setup.

Keep references focused on the intended character. The output scene can change
their poses while the prompt asks Qwen to retain their recognizable identities.
