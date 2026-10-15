import tempfile
import unittest
from pathlib import Path

from birthday_stickers.flatten_images import (
    DEFAULT_IMAGE_EXTENSIONS,
    find_images,
    flatten_and_rename_images,
    get_supported_extensions,
    main,
)


class TestFlattenImages(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_default_extensions(self):
        exts = get_supported_extensions()
        self.assertIn(".jpg", exts)
        self.assertIn(".png", exts)
        self.assertIn(".webp", exts)

        custom = get_supported_extensions("jpg, PNG, .gif")
        self.assertEqual(custom, {".jpg", ".png", ".gif"})

    def test_find_images_recursive(self):
        sub1 = self.root / "sub1"
        sub2 = self.root / "sub1" / "nested"
        sub1.mkdir(parents=True)
        sub2.mkdir(parents=True)

        (self.root / "root.jpg").touch()
        (sub1 / "img1.png").touch()
        (sub2 / "img2.webp").touch()
        (sub2 / "document.txt").touch()

        found = find_images(self.root, extensions=DEFAULT_IMAGE_EXTENSIONS)
        self.assertEqual(len(found), 3)
        names = [p.name for p in found]
        self.assertIn("root.jpg", names)
        self.assertIn("img1.png", names)
        self.assertIn("img2.webp", names)
        self.assertNotIn("document.txt", names)

    def test_flatten_and_rename_basic_move(self):
        sub1 = self.root / "characters"
        sub2 = self.root / "backgrounds" / "scenes"
        sub1.mkdir(parents=True)
        sub2.mkdir(parents=True)

        (sub1 / "charA.png").write_text("charA")
        (sub1 / "charB.jpg").write_text("charB")
        (sub2 / "scene1.PNG").write_text("scene1")

        plan = flatten_and_rename_images(
            directory=self.root,
            start_number=1,
            clean_empty_dirs=True,
        )

        self.assertEqual(len(plan), 3)

        # Expected files in root: 1.png, 2.png, 3.jpg
        root_files = sorted([p.name for p in self.root.iterdir() if p.is_file()])
        self.assertEqual(root_files, ["1.png", "2.png", "3.jpg"])

        # Check that empty subdirectories were removed
        self.assertFalse(sub1.exists())
        self.assertFalse(sub2.exists())

    def test_flatten_and_rename_copy_mode(self):
        sub = self.root / "sub"
        sub.mkdir(parents=True)
        (sub / "original.jpg").write_text("data")

        plan = flatten_and_rename_images(
            directory=self.root,
            copy=True,
        )

        self.assertEqual(len(plan), 1)
        self.assertTrue((self.root / "1.jpg").exists())
        self.assertTrue((sub / "original.jpg").exists())

    def test_custom_start_number_and_digits(self):
        sub = self.root / "sub"
        sub.mkdir(parents=True)
        (sub / "a.jpg").touch()
        (sub / "b.png").touch()

        flatten_and_rename_images(
            directory=self.root,
            start_number=10,
            digits=3,
        )

        root_files = sorted([p.name for p in self.root.iterdir() if p.is_file()])
        self.assertEqual(root_files, ["010.jpg", "011.png"])

    def test_collision_safe_renaming(self):
        # Suppose root already has a file named 1.jpg and sub has a file named 1.jpg
        sub = self.root / "sub"
        sub.mkdir(parents=True)
        (self.root / "1.jpg").write_text("root_one")
        (sub / "1.jpg").write_text("sub_one")

        flatten_and_rename_images(
            directory=self.root,
            start_number=1,
        )

        root_files = sorted([p.name for p in self.root.iterdir() if p.is_file()])
        self.assertEqual(root_files, ["1.jpg", "2.jpg"])

    def test_dry_run_leaves_disk_unchanged(self):
        sub = self.root / "sub"
        sub.mkdir(parents=True)
        img = sub / "photo.png"
        img.write_text("content")

        plan = flatten_and_rename_images(
            directory=self.root,
            dry_run=True,
        )

        self.assertEqual(len(plan), 1)
        self.assertTrue(img.exists())
        self.assertFalse((self.root / "1.png").exists())
        self.assertTrue(sub.exists())

    def test_keep_empty_dirs(self):
        sub = self.root / "sub"
        sub.mkdir(parents=True)
        (sub / "img.jpg").touch()

        flatten_and_rename_images(
            directory=self.root,
            clean_empty_dirs=False,
        )

        self.assertTrue(sub.exists())
        self.assertTrue((self.root / "1.jpg").exists())

    def test_invalid_directory(self):
        non_existent = self.root / "not_there"
        with self.assertRaises(FileNotFoundError):
            flatten_and_rename_images(directory=non_existent)

        not_a_dir = self.root / "file.txt"
        not_a_dir.touch()
        with self.assertRaises(NotADirectoryError):
            flatten_and_rename_images(directory=not_a_dir)

    def test_main_cli(self):
        sub = self.root / "sub"
        sub.mkdir(parents=True)
        (sub / "test.jpg").touch()

        exit_code = main([str(self.root), "--quiet"])
        self.assertEqual(exit_code, 0)
        self.assertTrue((self.root / "1.jpg").exists())


if __name__ == "__main__":
    unittest.main()
