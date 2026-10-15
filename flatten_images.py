from pathlib import Path
from birthday_stickers.flatten_images import flatten_and_rename_images

# Returns a list of (source_path, destination_path) tuples
plan = flatten_and_rename_images(
    directory=Path("output/s"),
    start_number=1,
    digits=3,               # e.g., 001.jpg, 002.png
    copy=False,             # Set True to keep originals
    clean_empty_dirs=True,  # Automatically cleans up empty subdirectories
    dry_run=False,
)

print(f"Processed {len(plan)} files.")