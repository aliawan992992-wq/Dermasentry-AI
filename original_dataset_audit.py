# STEP 119: Audit Individual Original Datasets
# PURPOSE: List class names and image counts for each original dataset.
# BENEFIT: Exclude merged, split, augmented, and generated collections.

from pathlib import Path
from collections import Counter, defaultdict
import xml.etree.ElementTree as ET
import csv
import re

ROOT = Path(r"D:\FYP\bite datasets")
OUTPUT = ROOT / "dataset_reports"
OUTPUT.mkdir(parents=True, exist_ok=True)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

# Exclude folders made by combining or processing datasets.
EXCLUDE_WORDS = [
    "updated dataset",
    "updated data",
    "merged",
    "merge",
    "dataset_split",
    "dataset split",
    "dual dataset",
    "clean_classification",
    "dermasentry_training",
    "augmented",
    "dataset_reports",
    "web_image_collection",
    "review",
    "candidate",
    "processed",
    "preprocessed",
    "balanced",
    "final dataset",
    "backup",
    "copy"
]

SPLIT_NAMES = {
    "train", "training", "valid",
    "validation", "val", "test", "testing"
}

def normalize(text):
    return re.sub(
        r"\s+", " ",
        text.lower().replace("_", " ").replace("-", " ")
    ).strip()


def excluded(name):
    name = normalize(name)
    return any(
        normalize(word) in name
        for word in EXCLUDE_WORDS
    )


def count_classification(folder, images):
    """
    Determine class labels from directory structure.
    Expected examples:
      dataset/train/chigger/img.jpg
      dataset/chigger/img.jpg
    """
    result = Counter()
    found = 0

    for image in images:
        parts = image.relative_to(folder).parts[:-1]
        candidates = [
            p for p in parts
            if normalize(p) not in SPLIT_NAMES
        ]

        if not candidates:
            continue

        # Choose the class closest to the image.
        label = candidates[-1]

        if normalize(label) in {
            "images", "image", "imgs",
            "photos", "photo"
        }:
            if len(candidates) >= 2:
                label = candidates[-2]
            else:
                continue

        result[label] += 1
        found += 1

    return result, found


def count_voc(folder):
    """
    For object detection, count images containing each class.
    An image with 3 boxes of the same class counts only once.
    """
    class_counts = Counter()
    annotated_images = set()
    xml_count = 0

    for xml in folder.rglob("*.xml"):
        try:
            tree = ET.parse(xml)

            labels = {
                node.text.strip()
                for node in tree.findall(".//object/name")
                if node.text and node.text.strip()
            }

            if not labels:
                continue

            xml_count += 1
            annotated_images.add(str(xml.with_suffix("")))

            for label in labels:
                class_counts[label] += 1

        except Exception:
            continue

    return class_counts, xml_count, len(annotated_images)


all_rows = []
details = []

print("=" * 85)
print("DERMASENTRY AI - ORIGINAL DATASET AUDIT")
print("=" * 85)

for folder in sorted(ROOT.iterdir()):

    if not folder.is_dir():
        continue

    if excluded(folder.name):
        print("SKIPPED:", folder.name)
        continue

    images = [
        p for p in folder.rglob("*")
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    ]

    if not images:
        continue

    xml_files = list(folder.rglob("*.xml"))

    if xml_files:
        dataset_type = "Object Detection (Pascal VOC)"
        class_counts, annotated, _ = count_voc(folder)
    else:
        dataset_type = "Folder Classification"
        class_counts, annotated = count_classification(
            folder, images
        )

    print("\n" + "-" * 85)
    print("DATASET:", folder.name)
    print("TYPE:", dataset_type)
    print("TOTAL IMAGE FILES:", len(images))
    print("TOTAL CLASSES:", len(class_counts))
    print("CLASSES:")

    if class_counts:
        for class_name, count in class_counts.most_common():
            print(f"  {class_name:<30} {count}")
            all_rows.append({
                "dataset": folder.name,
                "type": dataset_type,
                "total_images": len(images),
                "total_classes": len(class_counts),
                "class_name": class_name,
                "class_image_count": count
            })
    else:
        print("  No class labels identified")

    details.append({
        "dataset": folder.name,
        "type": dataset_type,
        "images": len(images),
        "classes": len(class_counts),
        "labeled_images": annotated
    })


# Save class-wise report.
csv_path = OUTPUT / "original_datasets_class_counts.csv"

with csv_path.open(
    "w", newline="", encoding="utf-8-sig"
) as f:

    writer = csv.DictWriter(
        f,
        fieldnames=[
            "dataset", "type", "total_images",
            "total_classes", "class_name",
            "class_image_count"
        ]
    )

    writer.writeheader()
    writer.writerows(all_rows)


# Save summary report.
summary_path = OUTPUT / "original_datasets_summary.txt"

with summary_path.open(
    "w", encoding="utf-8"
) as f:

    f.write("DERMASENTRY ORIGINAL DATASET REPORT\n")
    f.write("=" * 80 + "\n\n")

    for item in details:
        f.write(
            f"{item['dataset']}\n"
            f"Type: {item['type']}\n"
            f"Images: {item['images']}\n"
            f"Classes: {item['classes']}\n"
            f"Labeled: {item['labeled_images']}\n\n"
        )


print("\n" + "=" * 85)
print("FINAL DATASET AUDIT SUMMARY")
print("=" * 85)

print("INDIVIDUAL DATASETS:", len(details))
print("TOTAL IMAGE FILES:", sum(x["images"] for x in details))
print("TOTAL DISTINCT DATASET-CLASS PAIRS:", len(all_rows))

print("\nCSV REPORT:", csv_path)
print("TXT REPORT:", summary_path)
print("ALL ORIGINAL DATASETS: UNCHANGED")