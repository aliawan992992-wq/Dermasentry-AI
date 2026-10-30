from pathlib import Path
from io import BytesIO
from collections import Counter
import csv
import hashlib
import html
import re
import time

import requests
import imagehash
from PIL import Image, ImageOps

DATASET = Path(r"D:\FYP\bite datasets\dataset_split")
ROOT = Path(r"D:\FYP\bite datasets\web_image_collection")
OUT = ROOT / "candidates"
NEAR = ROOT / "near_duplicate_review"
REPORT = ROOT / "image_collection_report.csv"

API = "https://en.wikipedia.org/w/api.php"

SEARCHES = {
    "chigger": [
        "chigger bites skin",
        "trombiculiasis",
        "chigger bite rash"
    ],
    "flea": [
        "flea bites skin",
        "pulicosis",
        "flea bite rash"
    ],
    "bee_wasp": [
        "wasp sting skin",
        "bee sting skin",
        "hornet sting skin"
    ],
    "other_insect_bite": [
        "gnat bites human skin",
        "horsefly bite skin",
        "midge bite skin"
    ]
}

LIMIT_PER_QUERY = 40
MAX_BYTES = 8 * 1024 * 1024
EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".jfif"}

ROOT.mkdir(parents=True, exist_ok=True)

session = requests.Session()
session.headers.update({
    "User-Agent": "DermaSentryAcademicDatasetResearch/1.0 (educational image review)"
})

def meta_value(metadata, key):
    return html.unescape(
        metadata.get(key, {}).get("value", "")
    ).strip()

def allowed_license(value):
    value = re.sub(r"\s+", " ", value.strip().lower())

    if re.search(r"\b(nc|nd|noncommercial|non-commercial)\b", value):
        return False

    return (
        value.startswith("cc by ")
        or value.startswith("cc by-sa ")
        or value in {"cc0", "cc0 1.0", "public domain"}
        or value.startswith("public domain ")
        or value.startswith("pd-")
    )

def search_images(query):
    response = session.get(
        API,
        params={
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": query,
            "gsrnamespace": 6,
            "gsrlimit": LIMIT_PER_QUERY,
            "prop": "imageinfo",
            "iiprop": "url|size|mime|extmetadata",
            "iiextmetadatafilter": (
                "LicenseShortName|LicenseUrl|Artist|Credit"
            )
        },
        timeout=40
    )
    response.raise_for_status()
    return list(response.json().get("query", {}).get("pages", {}).values())

print("DERMASENTRY AI - IMAGE COLLECTION", flush=True)
print("=" * 60, flush=True)

existing_sha = set()
existing_phash = []

for split in ("train", "val", "test"):
    folder = DATASET / split
    if not folder.is_dir():
        raise FileNotFoundError(f"Missing dataset folder: {folder}")

    files = [
        p for p in folder.rglob("*")
        if p.is_file() and p.suffix.lower() in EXTENSIONS
    ]

    print(f"Checking {split}: {len(files)} images", flush=True)

    for index, path in enumerate(files, 1):
        try:
            content = path.read_bytes()
            existing_sha.add(hashlib.sha256(content).hexdigest())

            with Image.open(BytesIO(content)) as image:
                corrected = ImageOps.exif_transpose(image)
                existing_phash.append(imagehash.phash(corrected.convert("RGB")))
        except Exception as error:
            print(f"Image decode issue: {path}: {error}")

        if index % 1000 == 0:
            print(f"  Checked {index}/{len(files)}", flush=True)

print("Existing unique exact hashes:", len(existing_sha), flush=True)

seen_urls = set()
new_sha = set()
new_phash = []
rows = []

fieldnames = [
    "class_candidate", "query", "title", "status",
    "source_page", "license", "license_url",
    "artist_html", "credit_html", "sha256",
    "file_path"
]

def save_report():
    with REPORT.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

for class_name, terms in SEARCHES.items():

    (OUT / class_name).mkdir(parents=True, exist_ok=True)
    (NEAR / class_name).mkdir(parents=True, exist_ok=True)

    print(f"\nSEARCHING CLASS: {class_name}", flush=True)

    for term in terms:
        print("Query:", term, flush=True)

        try:
            results = search_images(term)
        except Exception as error:
            print("Search error:", error, flush=True)
            continue

        for page in results:
            infos = page.get("imageinfo", [])
            if not infos:
                continue

            info = infos[0]
            url = info.get("url", "")

            if not url or url in seen_urls:
                continue

            seen_urls.add(url)

            metadata = info.get("extmetadata", {})
            license_name = meta_value(metadata, "LicenseShortName")

            row = {
                "class_candidate": class_name,
                "query": term,
                "title": page.get("title", ""),
                "status": "",
                "source_page": info.get("descriptionurl", ""),
                "license": license_name,
                "license_url": meta_value(metadata, "LicenseUrl"),
                "artist_html": meta_value(metadata, "Artist"),
                "credit_html": meta_value(metadata, "Credit"),
                "sha256": "",
                "file_path": ""
            }

            try:
                if not allowed_license(license_name):
                    row["status"] = "LICENSE_REVIEW"

                elif info.get("mime") not in (
                    "image/jpeg", "image/png", "image/webp"
                ):
                    row["status"] = "UNSUPPORTED_FORMAT"

                elif info.get("size", 0) > MAX_BYTES:
                    row["status"] = "TOO_LARGE"

                elif not url.startswith("https://"):
                    row["status"] = "INVALID_URL"

                else:
                    response = session.get(url, timeout=45)
                    response.raise_for_status()

                    raw = response.content

                    if len(raw) > MAX_BYTES:
                        row["status"] = "TOO_LARGE"
                    else:
                        sha = hashlib.sha256(raw).hexdigest()
                        row["sha256"] = sha

                        if sha in existing_sha or sha in new_sha:
                            row["status"] = "EXACT_DUPLICATE"

                        else:
                            with Image.open(BytesIO(raw)) as image:
                                image = ImageOps.exif_transpose(image)
                                image.load()

                                if min(image.size) < 160:
                                    raise ValueError("Image too small")

                                phash = imagehash.phash(image.convert("RGB"))

                                near_existing = any(
                                    phash - previous <= 5
                                    for previous in existing_phash
                                )
                                near_new = any(
                                    phash - previous <= 5
                                    for previous in new_phash
                                )

                                if near_existing or near_new:
                                    folder = NEAR / class_name
                                    row["status"] = "NEAR_DUPLICATE_REVIEW"
                                else:
                                    folder = OUT / class_name
                                    row["status"] = "CANDIDATE_REVIEW"

                                target = folder / f"{sha[:24]}.jpg"
                                image.convert("RGB").save(
                                    target, "JPEG", quality=95
                                )

                                row["file_path"] = str(target)

                            new_sha.add(sha)
                            new_phash.append(phash)

            except Exception as error:
                row["status"] = "ERROR"
                print("  Error:", error, flush=True)

            rows.append(row)

            if row["status"] in (
                "CANDIDATE_REVIEW", "NEAR_DUPLICATE_REVIEW"
            ):
                print(
                    f"  {row['status']}: {row['title']}",
                    flush=True
                )

            time.sleep(0.5)

        save_report()

    save_report()

print("\n" + "=" * 60)
print("DERMASENTRY AI - COLLECTION SUMMARY")
print("=" * 60)

for status, count in sorted(Counter(r["status"] for r in rows).items()):
    print(f"{status}: {count}")

print("\nNEW CANDIDATES BY CLASS")

for class_name in SEARCHES:
    count = sum(
        r["class_candidate"] == class_name
        and r["status"] == "CANDIDATE_REVIEW"
        for r in rows
    )
    print(f"{class_name}: {count}")

print("\nREPORT:", REPORT)
print("CANDIDATES:", OUT)
print("ORIGINAL DATASET: UNCHANGED")

