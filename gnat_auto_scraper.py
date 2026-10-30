from pathlib import Path
from collections import Counter
from urllib.parse import urljoin
import requests
import hashlib
import csv
import re
import io
import time
import html

from PIL import Image, ImageOps, ImageDraw
import imagehash

ROOT = Path(r"D:\FYP\bite datasets")
WEB = ROOT / "web_image_collection"

OUTPUT = WEB / "gnat_bite_new_dataset"
IMAGES = OUTPUT / "gnat_bite"
IMAGES.mkdir(parents=True, exist_ok=True)

MANIFEST = OUTPUT / "gnat_manifest.csv"

MAX_NEW = 150
MIN_SIZE = 224
PHASH_THRESHOLD = 5

EXTS = {".jpg", ".jpeg", ".png", ".webp"}

session = requests.Session()
session.headers.update({
    "User-Agent": "DermaSentryResearch/1.0 (academic image review)"
})

SEARCH_TERMS = [
    "gnat bite skin",
    "gnat bites human",
    "biting midge bites human",
    "Culicoides bite skin",
    "ceratopogonidae bite skin",
    "midge bite lesions",
    "midge bite rash",
    "biting midges dermatitis"
]

GNAT_TERMS = (
    r"\bgnats?\b|biting[\s-]?midges?|"
    r"\bculicoides\b|ceratopogon"
)

SKIN_TERMS = (
    r"human|patient|person|skin|cutaneous|"
    r"hand|arm|leg|ankle|foot|feet|"
    r"papule|rash|lesion|erythema|swelling"
)

BITE_TERMS = (
    r"\bbites?\b|\bbitten\b|"
    r"\bbiting\b|reaction|dermatitis|"
    r"rash|papules?|lesions?|welts?"
)

EXCLUDE_TERMS = (
    r"microscope|micrograph|histology|"
    r"electron microscopy|SEM image|"
    r"larva|larvae|pupa|eggs|"
    r"diagram|chart|graph|schematic|"
    r"\bmouse\b|\bmice\b|\brat\b"
)

FIELDS = [
    "filename", "source", "source_page",
    "image_url", "label", "evidence",
    "license", "attribution", "sha256",
    "width", "height", "review_status"
]

rows = []

if MANIFEST.exists():
    with MANIFEST.open(
        "r", encoding="utf-8-sig", newline=""
    ) as file:
        rows = list(csv.DictReader(file))

rows = [
    row for row in rows
    if (IMAGES / row["filename"]).exists()
]

known_sha = set()
known_phash = []
known_sources = {
    row["image_url"] for row in rows
}

stats = Counter()

print("=" * 65)
print("DERMASENTRY AI - GNAT BITE AUTO SCRAPER")
print("=" * 65)

print("Scanning existing datasets...", flush=True)

REFERENCE = [
    ROOT / "dataset_split",
    WEB / "class_labeled_images",
    WEB / "nih_bulk_review_candidates",
    WEB / "scin_general_insect_bite",
    WEB / "new_labeled_review_candidates",
    WEB / "quality_review",
    WEB / "gnat_bite_new_dataset"
]

scanned_paths = set()

for folder in REFERENCE:
    if not folder.exists():
        continue

    for path in folder.rglob("*"):
        if not path.is_file():
            continue

        if path.suffix.lower() not in EXTS:
            continue

        identity = str(path.resolve()).lower()

        if identity in scanned_paths:
            continue

        scanned_paths.add(identity)

        try:
            raw = path.read_bytes()

            known_sha.add(
                hashlib.sha256(raw).hexdigest()
            )

            with Image.open(io.BytesIO(raw)) as img:
                known_phash.append(
                    imagehash.phash(
                        ImageOps.exif_transpose(img).convert("RGB")
                    )
                )
        except Exception:
            stats["reference_errors"] += 1

print(
    "Reference image files scanned:",
    len(scanned_paths),
    flush=True
)


def clean(value):
    value = re.sub(
        r"<[^>]+>", " ", str(value or "")
    )
    return re.sub(
        r"\s+", " ", html.unescape(value)
    ).strip()


def relevant(text):
    if not re.search(GNAT_TERMS, text, re.I):
        return False
    if not re.search(SKIN_TERMS, text, re.I):
        return False
    if not re.search(BITE_TERMS, text, re.I):
        return False
    if re.search(EXCLUDE_TERMS, text, re.I):
        return False
    return True


def allowed_license(value):
    low = value.lower().replace(
        "https://", ""
    ).replace("http://", "")

    if "creativecommons.org/publicdomain/zero/" in low:
        return True

    if "creativecommons.org/publicdomain/mark/" in low:
        return True

    if re.search(
        r"creativecommons\.org/licenses/by/(?:2\.0|3\.0|4\.0)",
        low
    ):
        return True

    if re.search(
        r"creativecommons\.org/licenses/by-sa/(?:2\.0|3\.0|4\.0)",
        low
    ):
        return True

    return False


def write_manifest():
    with MANIFEST.open(
        "w", encoding="utf-8", newline=""
    ) as file:
        writer = csv.DictWriter(
            file, fieldnames=FIELDS
        )
        writer.writeheader()
        writer.writerows(rows)


def save_candidate(source, page, url, license_url,
                   attribution, evidence, identifier):

    if len(rows) >= MAX_NEW:
        return

    if not allowed_license(license_url):
        stats["license_rejected"] += 1
        return

    if not relevant(evidence):
        stats["label_rejected"] += 1
        return

    if url in known_sources:
        stats["already_collected"] += 1
        return

    try:
        response = session.get(url, timeout=25)
        response.raise_for_status()

        if not response.headers.get(
            "Content-Type", ""
        ).lower().startswith("image/"):
            stats["non_image"] += 1
            return

        raw = response.content

        if len(raw) > 12_000_000:
            stats["large_file"] += 1
            return

        with Image.open(io.BytesIO(raw)) as img:
            img.load()
            image_format = img.format

            if image_format not in {
                "JPEG", "PNG", "WEBP"
            }:
                stats["unsupported"] += 1
                return

            img = ImageOps.exif_transpose(img)
            width, height = img.size

            if min(width, height) < MIN_SIZE:
                stats["small"] += 1
                return

            phash = imagehash.phash(
                img.convert("RGB")
            )

        sha = hashlib.sha256(raw).hexdigest()

        if sha in known_sha:
            stats["exact_duplicate"] += 1
            return

        if any(
            phash - old <= PHASH_THRESHOLD
            for old in known_phash
        ):
            stats["near_duplicate"] += 1
            return

        extension = {
            "JPEG": ".jpg",
            "PNG": ".png",
            "WEBP": ".webp"
        }[image_format]

        filename = (
            f"GNAT_{source}_{identifier}_"
            f"{sha[:12]}{extension}"
        )

        filename = re.sub(
            r"[^A-Za-z0-9_.-]", "_", filename
        )

        target = IMAGES / filename
        target.write_bytes(raw)

        known_sha.add(sha)
        known_phash.append(phash)
        known_sources.add(url)

        rows.append({
            "filename": filename,
            "source": source,
            "source_page": page,
            "image_url": url,
            "label": "gnat_bite_candidate",
            "evidence": evidence[:1500],
            "license": license_url,
            "attribution": attribution[:700],
            "sha256": sha,
            "width": width,
            "height": height,
            "review_status": "NEEDS_VISUAL_AND_LABEL_REVIEW"
        })

        write_manifest()

        print(
            f"SAVED [{len(rows)}/{MAX_NEW}]:",
            filename,
            flush=True
        )

    except Exception as error:
        stats["download_error"] += 1
        print(
            "Download failed:",
            str(error)[:100]
        )


# --------------------------------------------------
# SOURCE 1: WIKIMEDIA COMMONS
# --------------------------------------------------

print("\nSOURCE 1: WIKIMEDIA COMMONS", flush=True)

COMMONS_API = (
    "https://commons.wikimedia.org/w/api.php"
)

for query in SEARCH_TERMS:

    if len(rows) >= MAX_NEW:
        break

    print("SEARCH:", query, flush=True)

    try:
        response = session.get(
            COMMONS_API,
            params={
                "action": "query",
                "format": "json",
                "generator": "search",
                "gsrsearch": query,
                "gsrnamespace": 6,
                "gsrlimit": 50,
                "prop": "imageinfo",
                "iiprop": "url|extmetadata",
                "iilimit": 1
            },
            timeout=20
        )

        response.raise_for_status()
        data = response.json()

        pages = (
            data.get("query", {})
            .get("pages", {})
        )

        for item in pages.values():

            info = (item.get("imageinfo") or [])

            if not info:
                continue

            info = info[0]
            meta = info.get("extmetadata") or {}

            def metadata(name):
                return clean(
                    (meta.get(name) or {}).get("value")
                )

            title = item.get("title", "")

            evidence = " ".join([
                title,
                metadata("ImageDescription")
            ])

            license_url = metadata("LicenseUrl")

            author = metadata("Artist")

            image_url = info.get("url", "")

            page_url = (
                info.get("descriptionurl") or ""
            )

            if not image_url:
                continue

            save_candidate(
                "COMMONS",
                page_url,
                image_url,
                license_url,
                author,
                evidence,
                str(item.get("pageid", "unknown"))
            )

    except Exception as error:
        stats["commons_search_error"] += 1
        print("Commons error:", str(error)[:150])

    time.sleep(1)


# --------------------------------------------------
# SOURCE 2: NIH OPEN-I
# --------------------------------------------------

print("\nSOURCE 2: NIH OPEN-I", flush=True)

OPENI_API = "https://openi.nlm.nih.gov/api/search"

NIH_QUERIES = [
    "biting midge bites",
    "Culicoides bite reaction",
    "gnat bites human",
    "midge bite skin",
    "ceratopogonidae bite",
    "Culicoides dermatitis"
]

for query in NIH_QUERIES:

    if len(rows) >= MAX_NEW:
        break

    print("SEARCH:", query, flush=True)

    for page in range(3):

        try:
            response = session.get(
                OPENI_API,
                params={
                    "query": query,
                    "m": page * 100 + 1,
                    "n": 100
                },
                timeout=25
            )

            response.raise_for_status()
            data = response.json()

            items = data.get("list") or []

            if not items:
                break

            for item in items:

                if len(rows) >= MAX_NEW:
                    break

                figure = item.get("image") or {}
                caption = clean(
                    figure.get("caption")
                )

                if not relevant(caption):
                    continue

                pmcid = str(
                    item.get("pmcid") or ""
                ).replace("PMC", "")

                license_url = str(
                    item.get("licenseURL") or ""
                )

                img_path = item.get("imgLarge")

                if not pmcid.isdigit() or not img_path:
                    continue

                image_url = urljoin(
                    "https://openi.nlm.nih.gov",
                    img_path
                )

                article_url = (
                    "https://pmc.ncbi.nlm.nih.gov/"
                    f"articles/PMC{pmcid}/"
                )

                identifier = (
                    f"PMC{pmcid}_"
                    f"{hashlib.md5(image_url.encode()).hexdigest()[:8]}"
                )

                save_candidate(
                    "NIH",
                    article_url,
                    image_url,
                    license_url,
                    clean(item.get("title")),
                    caption,
                    identifier
                )

            if len(items) < 100:
                break

        except Exception as error:
            stats["nih_search_error"] += 1
            print("NIH error:", str(error)[:150])
            break

        time.sleep(1)


# --------------------------------------------------
# BUILD VISUAL REVIEW SHEETS
# --------------------------------------------------

print("\nCREATING REVIEW SHEETS...", flush=True)

for start in range(0, len(rows), 24):

    batch = rows[start:start + 24]

    sheet = Image.new(
        "RGB", (1200, 900), "white"
    )

    draw = ImageDraw.Draw(sheet)

    for index, row in enumerate(batch):

        path = IMAGES / row["filename"]

        col = index % 4
        line = index // 4

        x = col * 300
        y = line * 150

        try:
            with Image.open(path) as img:
                preview = ImageOps.contain(
                    img.convert("RGB"),
                    (280, 115)
                )

            sheet.paste(
                preview, (x + 5, y + 25)
            )

            draw.text(
                (x + 5, y + 5),
                row["filename"][:35],
                fill="black"
            )

        except Exception:
            pass

    sheet.save(
        OUTPUT / f"REVIEW_{start // 24 + 1}.jpg",
        quality=90
    )


print("\n" + "=" * 65)
print("GNAT SCRAPING SUMMARY")
print("=" * 65)

print("TOTAL NEW CANDIDATES:", len(rows))
print("SKIPPED:", dict(stats))
print("IMAGE FOLDER:", IMAGES)
print("MANIFEST:", MANIFEST)
print("ORIGINAL DATASET: UNCHANGED")
print("FINAL CLINICAL VERIFICATION: REQUIRED")
