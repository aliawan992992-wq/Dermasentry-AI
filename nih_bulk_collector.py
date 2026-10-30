import csv
import re
import io
import time
import hashlib
from pathlib import Path
from collections import Counter
from urllib.parse import urljoin

import requests
import imagehash
from PIL import Image, ImageOps, ImageDraw

ROOT = Path(r"D:\FYP\bite datasets")
BASE = ROOT / "web_image_collection"

OUTPUT = BASE / "nih_bulk_review_candidates"
DATASET = ROOT / "dataset_split"
EXISTING = BASE / "class_labeled_images"

API = "https://openi.nlm.nih.gov/api/search"
MAX_NEW = 100
MAX_PER_CLASS = 30
MAX_PAGES = 5
PAGE_SIZE = 100
MIN_SIZE = 224

QUERIES = {
    "chigger": [
        "chigger bite",
        "trombiculiasis",
        "harvest mite dermatitis",
        "trombiculid mite bite",
        "chigger skin lesion"
    ],
    "flea": [
        "flea bite",
        "pulicosis",
        "flea bite papules",
        "ctenocephalides human bite",
        "flea bite dermatitis"
    ],
    "bee_wasp": [
        "bee sting",
        "wasp sting",
        "hornet sting",
        "hymenoptera sting",
        "yellow jacket sting",
        "wasp sting skin lesion"
    ],
    "other_insect_bite": [
        "sand fly bite",
        "black fly bite",
        "biting midge bite",
        "horsefly bite",
        "phlebotomus bite",
        "sergentomyia bite",
        "sandfly skin reaction"
    ]
}

LABEL_WORDS = {
    "chigger": r"chigger|trombicul|harvest mite",
    "flea": r"\bflea\b|\bfleas\b|pulicosis|ctenocephalides",
    "bee_wasp": r"\bbee\b|honeybee|\bwasp\b|hornet|hymenopter|yellow jacket",
    "other_insect_bite": r"sand[\s-]?fl|black[\s-]?fl|biting midge|horse[\s-]?fl|phlebotomus|sergentomyia"
}

SKIN_WORDS = (
    r"skin|cutaneous|patient|human|volunteer|"
    r"arm|hand|leg|foot|feet|neck|face|"
    r"papule|lesion|erythema|swelling|"
    r"rash|blister|stung site|bite site"
)

REACTION_WORDS = (
    r"bite|bitten|sting|stung|"
    r"lesion|erythema|papule|blister|"
    r"swelling|rash|reaction"
)

REJECT_WORDS = (
    r"\bmouse\b|\bmice\b|\brat\b|"
    r"histology|microscop|electron micrograph|"
    r"western blot|graph|schematic|chart|"
    r"radiograph|ultrasound|MRI|CT scan|"
    r"angiogram|illustration|flowchart"
)

LICENSE_PATTERN = re.compile(
    r"creativecommons\.org/"
    r"(?:licenses/by/(?:2\.0|3\.0|4\.0)"
    r"|publicdomain/(?:zero/1\.0|mark/1\.0))",
    re.I
)

FIELDS = [
    "class", "filename", "pmcid", "title",
    "caption", "article_url", "image_url",
    "license", "query", "width", "height",
    "sha256", "status"
]

OUTPUT.mkdir(parents=True, exist_ok=True)

for category in QUERIES:
    (OUTPUT / category).mkdir(parents=True, exist_ok=True)

session = requests.Session()
session.headers["User-Agent"] = (
    "DermaSentryAI/1.0 Academic Image Research"
)

manifest = OUTPUT / "image_manifest.csv"
rows = []

if manifest.exists():
    with manifest.open(
        "r", encoding="utf-8-sig", newline=""
    ) as file:
        rows = list(csv.DictReader(file))

rows = [
    row for row in rows
    if (
        OUTPUT / row["class"] / row["filename"]
    ).exists()
]

known_hashes = set()
known_phashes = []

print("=" * 65)
print("DERMASENTRY AI - BULK IMAGE COLLECTION")
print("=" * 65)
print("Checking existing images for duplicates...")

scan_locations = [
    DATASET,
    EXISTING,
    OUTPUT,
    BASE / "quality_review",
    BASE / "nih_visual_review",
    BASE / "nih_high_resolution_review",
    BASE / "scin_general_insect_bite"
]

scanned = set()

for folder in scan_locations:
    if not folder.exists():
        continue

    for path in folder.rglob("*"):
        if not path.is_file():
            continue

        if path.suffix.lower() not in (
            ".jpg", ".jpeg", ".png", ".webp"
        ):
            continue

        identity = str(path.resolve()).lower()

        if identity in scanned:
            continue

        scanned.add(identity)

        try:
            raw = path.read_bytes()
            known_hashes.add(
                hashlib.sha256(raw).hexdigest()
            )

            with Image.open(io.BytesIO(raw)) as img:
                known_phashes.append(
                    imagehash.phash(img.convert("RGB"))
                )

        except Exception:
            continue

print("Existing images scanned:", len(scanned))

counts = Counter(row["class"] for row in rows)
existing_urls = {row["image_url"] for row in rows}
seen_urls = set(existing_urls)
stats = Counter()
downloaded = 0


def clean_text(value):
    value = re.sub(r"<[^>]+>", " ", str(value or ""))
    return re.sub(r"\s+", " ", value).strip()


def save_manifest():
    with manifest.open(
        "w", encoding="utf-8", newline=""
    ) as file:
        writer = csv.DictWriter(
            file, fieldnames=FIELDS
        )
        writer.writeheader()
        writer.writerows(rows)


def relevant_figure(item, category):
    image_info = item.get("image") or {}
    caption = clean_text(
        image_info.get("caption")
    )

    title = clean_text(item.get("title"))

    if not caption or len(caption) < 20:
        return None

    if not re.search(
        LABEL_WORDS[category], caption, re.I
    ):
        return None

    if not re.search(
        SKIN_WORDS, caption, re.I
    ):
        return None

    if not re.search(
        REACTION_WORDS, caption, re.I
    ):
        return None

    if re.search(
        REJECT_WORDS, caption, re.I
    ):
        return None

    combined = title + " " + caption

    if category == "chigger":
        if re.search(
            r"scrub typhus|orientia|tsutsugamushi|eschar",
            combined, re.I
        ):
            return None

    if category == "other_insect_bite":
        if re.search(
            r"mosquito|bed bug|tick|chigger|flea|"
            r"spider|wasp|hornet|bee|ant|"
            r"leishmaniasis|leishmania",
            combined, re.I
        ):
            return None

    license_url = str(
        item.get("licenseURL") or ""
    )

    if not LICENSE_PATTERN.search(license_url):
        return None

    image_path = item.get("imgLarge")

    if not image_path:
        return None

    pmcid = str(
        item.get("pmcid") or ""
    ).replace("PMC", "")

    if not pmcid.isdigit():
        return None

    return {
        "caption": caption,
        "title": title,
        "pmcid": pmcid,
        "license": license_url,
        "url": urljoin(
            "https://openi.nlm.nih.gov",
            image_path
        ),
        "article_url": (
            "https://pmc.ncbi.nlm.nih.gov/articles/"
            f"PMC{pmcid}/"
        )
    }


for category, queries in QUERIES.items():

    print("\n" + "=" * 65)
    print("CLASS:", category)
    print("=" * 65)

    for query in queries:

        if downloaded >= MAX_NEW:
            break

        if counts[category] >= MAX_PER_CLASS:
            break

        print("\nSEARCHING:", query, flush=True)

        for page in range(MAX_PAGES):

            if downloaded >= MAX_NEW:
                break

            if counts[category] >= MAX_PER_CLASS:
                break

            offset = page * PAGE_SIZE + 1

            try:
                response = session.get(
                    API,
                    params={
                        "query": query,
                        "m": offset,
                        "n": PAGE_SIZE
                    },
                    timeout=25
                )

                response.raise_for_status()
                data = response.json()

            except Exception as error:
                print("Search error:", error)
                break

            items = data.get("list") or []

            if not items:
                break

            print(
                "API results:",
                len(items),
                "| Page:",
                page + 1,
                flush=True
            )

            for item in items:

                if downloaded >= MAX_NEW:
                    break

                if counts[category] >= MAX_PER_CLASS:
                    break

                info = relevant_figure(
                    item, category
                )

                if info is None:
                    stats["metadata_rejected"] += 1
                    continue

                image_url = info["url"]

                if image_url in seen_urls:
                    stats["already_seen"] += 1
                    continue

                seen_urls.add(image_url)

                try:
                    image_response = session.get(
                        image_url,
                        timeout=25
                    )

                    image_response.raise_for_status()

                    content_type = image_response.headers.get(
                        "Content-Type", ""
                    )

                    if not content_type.lower().startswith(
                        "image/"
                    ):
                        stats["not_image"] += 1
                        continue

                    raw = image_response.content

                    if len(raw) > 12_000_000:
                        stats["too_large"] += 1
                        continue

                    with Image.open(
                        io.BytesIO(raw)
                    ) as image:

                        image.load()

                        image_format = image.format

                        if image_format not in (
                            "JPEG", "PNG", "WEBP"
                        ):
                            stats["invalid_image"] += 1
                            continue

                        width, height = image.size

                        if min(width, height) < MIN_SIZE:
                            stats["small_image"] += 1
                            continue

                        current_phash = imagehash.phash(
                            image.convert("RGB")
                        )

                    sha = hashlib.sha256(
                        raw
                    ).hexdigest()

                    if sha in known_hashes:
                        stats["exact_duplicate"] += 1
                        continue

                    is_duplicate = any(
                        current_phash - previous <= 5
                        for previous in known_phashes
                    )

                    if is_duplicate:
                        stats["near_duplicate"] += 1
                        continue

                    extension = {
                        "JPEG": ".jpg",
                        "PNG": ".png",
                        "WEBP": ".webp"
                    }[image_format]

                    filename = (
                        f"NIH_{category}_"
                        f"PMC{info['pmcid']}_"
                        f"{sha[:14]}{extension}"
                    )

                    destination = (
                        OUTPUT / category / filename
                    )

                    destination.write_bytes(raw)

                    known_hashes.add(sha)
                    known_phashes.append(
                        current_phash
                    )

                    rows.append({
                        "class": category,
                        "filename": filename,
                        "pmcid": info["pmcid"],
                        "title": info["title"],
                        "caption": info["caption"],
                        "article_url": info["article_url"],
                        "image_url": image_url,
                        "license": info["license"],
                        "query": query,
                        "width": width,
                        "height": height,
                        "sha256": sha,
                        "status": (
                            "NEEDS_VISUAL_AND_"
                            "CLINICAL_LABEL_REVIEW"
                        )
                    })

                    counts[category] += 1
                    downloaded += 1

                    save_manifest()

                    print(
                        f"SAVED [{downloaded}/{MAX_NEW}]",
                        category,
                        filename,
                        flush=True
                    )

                except Exception as error:
                    stats["download_error"] += 1
                    print(
                        "Download error:",
                        str(error)[:120]
                    )

                time.sleep(0.5)

            if len(items) < PAGE_SIZE:
                break

            time.sleep(0.8)


print("\nCREATING VISUAL REVIEW SHEETS...")

for category in QUERIES:

    class_rows = [
        row for row in rows
        if row["class"] == category
    ]

    for start in range(0, len(class_rows), 24):

        batch = class_rows[start:start + 24]

        if not batch:
            continue

        columns = 4
        cell_width = 260
        cell_height = 250

        sheet_height = (
            (len(batch) + columns - 1) // columns
        ) * cell_height

        sheet = Image.new(
            "RGB",
            (columns * cell_width, sheet_height),
            "white"
        )

        draw = ImageDraw.Draw(sheet)

        for index, row in enumerate(batch):

            path = (
                OUTPUT /
                category /
                row["filename"]
            )

            x = (index % columns) * cell_width
            y = (index // columns) * cell_height

            try:
                with Image.open(path) as source:
                    preview = ImageOps.contain(
                        source.convert("RGB"),
                        (245, 195)
                    )

                sheet.paste(
                    preview,
                    (x + 5, y + 28)
                )

                draw.text(
                    (x + 5, y + 5),
                    f"{index + start + 1}. PMC{row['pmcid']}",
                    fill="black"
                )

                draw.text(
                    (x + 5, y + 230),
                    category,
                    fill="black"
                )

            except Exception:
                continue

        review_file = OUTPUT / (
            f"REVIEW_{category}_"
            f"{start // 24 + 1}.jpg"
        )

        sheet.save(
            review_file,
            quality=90
        )

        print("REVIEW SHEET:", review_file.name)


print("\n" + "=" * 65)
print("BULK COLLECTION SUMMARY")
print("=" * 65)

print("NEW IMAGE CANDIDATES:", downloaded)

for category in QUERIES:
    print(
        category,
        ":",
        counts[category]
    )

print("\nSKIPPED:")
for reason, count in stats.items():
    print(reason, ":", count)

print("\nMANIFEST:", manifest)
print("OUTPUT:", OUTPUT)
print("ORIGINAL DATASET: UNCHANGED")
print("CLINICAL VERIFICATION: STILL REQUIRED")
print("=" * 65)
