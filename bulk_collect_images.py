import requests
import re
import csv
import io
import time
import hashlib
from pathlib import Path
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from PIL import Image
import imagehash

ROOT = Path(r"D:\FYP\bite datasets")
BASE = ROOT / "web_image_collection"
DEST = BASE / "class_labeled_images"
DATASET = ROOT / "dataset_split"

SEARCH_URL = "https://wwwn.cdc.gov/phil/AdvancedSearch.aspx"
MAX_PAGES = 5
DELAY_SECONDS = 0.6

SEARCHES = {
    "chigger": [
        "chigger bites", "chigger", "harvest mite bites"
    ],
    "flea": [
        "flea bites", "flea bite", "cat flea bites"
    ],
    "bee_wasp": [
        "wasp sting", "bee sting", "yellow jacket sting",
        "hornet sting"
    ],
    "other_insect_bite": [
        "biting midge bites", "sand fly bites",
        "black fly bites", "horse fly bites"
    ]
}

TARGET_TERMS = {
    "chigger": r"chigger|harvest mite",
    "flea": r"flea|fleabite",
    "bee_wasp": r"wasp|bee sting|honeybee|yellow jacket|hornet",
    "other_insect_bite": (
        r"biting midge|sand ?fl(?:y|ies)|"
        r"black ?fl(?:y|ies)|horse ?fl(?:y|ies)"
    )
}

REACTION = re.compile(
    r"bite marks?|bites? (?:on|of|to|sustained|caused|inflicted)|"
    r"sting (?:site|on|to)|stung|"
    r"cutaneous lesions?|erythematous lesions?|"
    r"papular lesions?|bite wounds?",
    re.I
)
SKIN = re.compile(
    r"patient|human (?:skin|arm|hand|leg|body)|"
    r"skin surface|cutaneous|forearm|ankle|"
    r"buttocks|thigh|shoulder|wrist",
    re.I
)

session = requests.Session()
session.headers.update({
    "User-Agent": "DermaSentry-AcademicImageReview/1.0"
})

DEST.mkdir(parents=True, exist_ok=True)

def get_form(html):
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", method=re.compile("post", re.I))
    if form is None:
        raise ValueError("CDC search form missing")

    data = {}
    for field in form.find_all("input"):
        name = field.get("name")
        if name and field.get("type", "").lower() in ("hidden", "text"):
            data[name] = field.get("value", "")

    for select in form.find_all("select"):
        name = select.get("name")
        selected = select.find("option", selected=True)
        if name and selected:
            data[name] = selected.get("value", "")

    return form, data, soup

def search_records(term):
    page = session.get(SEARCH_URL, timeout=30)
    page.raise_for_status()
    form, data, _ = get_form(page.text)

    prefix = "ctl00$contentArea2$ucAdvanceSearch$"
    data[prefix + "tbKeywords"] = term
    data[prefix + "chkSet3$chkSet3_0"] = "Photo"
    data[prefix + "btnAdvSearch"] = "Search"

    result = session.post(
        urljoin(SEARCH_URL, form.get("action", "")),
        data=data, timeout=40
    )
    result.raise_for_status()

    ids = set()
    seen_pages = set()

    for page_number in range(1, MAX_PAGES + 1):
        current = set(re.findall(
            r"Details\.aspx\?pid=(\d+)", result.text, re.I
        ))

        signature = tuple(sorted(current))
        if signature in seen_pages:
            break
        seen_pages.add(signature)
        ids.update(current)

        print(
            f"  PAGE {page_number}: {len(current)} records",
            flush=True
        )

        form, data, soup = get_form(result.text)

        next_link = next(
            (
                a for a in soup.find_all("a")
                if "next" in a.get_text(" ", strip=True).lower()
                and "__doPostBack" in a.get("href", "")
            ),
            None
        )

        if next_link is None:
            break

        match = re.search(
            r"__doPostBack\('([^']+)','([^']*)'\)",
            next_link.get("href", "")
        )
        if not match:
            break

        data["__EVENTTARGET"] = match.group(1)
        data["__EVENTARGUMENT"] = match.group(2)

        result = session.post(
            urljoin(result.url, form.get("action", "")),
            data=data, timeout=40
        )
        result.raise_for_status()
        time.sleep(DELAY_SECONDS)

    return ids

def inspect_record(pid, category):
    page_url = f"https://wwwn.cdc.gov/phil/Details.aspx?pid={pid}"
    response = session.get(page_url, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    images = []
    for img in soup.find_all("img"):
        src = img.get("src", "")
        if "PHIL_Images" in src and "lores" in src.lower():
            images.append(urljoin(page_url, src))

    if not images:
        return None

    for tag in soup(["script", "style", "nav", "footer"]):
        tag.decompose()

    text = " ".join(soup.get_text(" ", strip=True).split())

    # A source label alone is not enough:
    # require an insect term, patient/skin context, and bite/sting reaction.
    # Search for all three within a short description span.
    label_re = re.compile(TARGET_TERMS[category], re.I)
    supported = False
    evidence = ""

    for match in label_re.finditer(text):
        start = max(0, match.start() - 300)
        end = min(len(text), match.end() + 350)
        context = text[start:end]

        if SKIN.search(context) and REACTION.search(context):
            supported = True
            evidence = context[:500]
            break

    if not supported:
        return None

    # These are preliminary checks, not definitive diagnosis verification.
    return {
        "image_url": images[0],
        "source_page": page_url,
        "evidence": evidence
    }

known_sha = set()
known_phash = []

print("CHECKING EXISTING IMAGES...", flush=True)

for directory in [
    DATASET,
    BASE / "class_labeled_images",
    BASE / "scin_general_insect_bite",
    BASE / "low_resolution_review",
    BASE / "quality_review",
    BASE / "cdc_skin_candidates",
    BASE / "cdc_test",
    BASE / "visual_review"
]:
    if not directory.exists():
        continue

    for path in directory.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in (
            ".jpg", ".jpeg", ".png", ".webp"
        ):
            continue

        try:
            data = path.read_bytes()
            known_sha.add(hashlib.sha256(data).hexdigest())
            with Image.open(io.BytesIO(data)) as image:
                known_phash.append(
                    imagehash.phash(image.convert("RGB"))
                )
        except Exception:
            continue

print("EXISTING HASHES:", len(known_sha), flush=True)

processed_ids = set()
saved = 0
skipped = 0
failed = 0

manifest = BASE / "bulk_collection_manifest.csv"
fields = [
    "filename", "class", "cdc_id", "source_page",
    "source_image", "label_evidence", "status"
]

with manifest.open("a", newline="", encoding="utf-8") as file:
    writer = csv.DictWriter(file, fieldnames=fields)

    if file.tell() == 0:
        writer.writeheader()

    for category, queries in SEARCHES.items():
        print(f"\nCLASS: {category}", flush=True)
        folder = DEST / category
        folder.mkdir(parents=True, exist_ok=True)

        found_ids = set()

        for term in queries:
            try:
                print("SEARCH:", term, flush=True)
                found_ids.update(search_records(term))
            except Exception as error:
                print("SEARCH FAILED:", str(error)[:180], flush=True)

            time.sleep(DELAY_SECONDS)

        print("UNIQUE RECORDS:", len(found_ids), flush=True)

        for pid in sorted(found_ids, key=int):
            if pid in processed_ids:
                continue
            processed_ids.add(pid)

            destination = folder / f"CDC_{category}_{pid}.jpg"

            if destination.exists():
                skipped += 1
                continue

            try:
                record = inspect_record(pid, category)
                if record is None:
                    skipped += 1
                    continue

                response = session.get(
                    record["image_url"], timeout=40
                )
                response.raise_for_status()

                raw = response.content

                with Image.open(io.BytesIO(raw)) as image:
                    image.load()
                    if min(image.size) < 224:
                        skipped += 1
                        continue
                    perceptual = imagehash.phash(
                        image.convert("RGB")
                    )

                sha = hashlib.sha256(raw).hexdigest()

                if sha in known_sha:
                    skipped += 1
                    continue

                if any(abs(perceptual - old) <= 5
                       for old in known_phash):
                    skipped += 1
                    continue

                # Save as source-labeled candidate, not clinical ground truth.
                destination.write_bytes(raw)
                known_sha.add(sha)
                known_phash.append(perceptual)

                writer.writerow({
                    "filename": destination.name,
                    "class": category,
                    "cdc_id": pid,
                    "source_page": record["source_page"],
                    "source_image": record["image_url"],
                    "label_evidence": record["evidence"],
                    "status": "candidate_needs_final_review"
                })
                file.flush()

                saved += 1
                print("SAVED:", destination.name, flush=True)

            except Exception as error:
                failed += 1
                print(
                    "FAILED:", pid, str(error)[:160],
                    flush=True
                )

            time.sleep(DELAY_SECONDS)

print("\n" + "=" * 55)
print("BULK COLLECTION SUMMARY")
print("=" * 55)
print("NEW CANDIDATES SAVED:", saved)
print("SKIPPED:", skipped)
print("FAILED:", failed)

for category in SEARCHES:
    folder = DEST / category
    count = len(list(folder.glob("*.jpg")))
    print(f"{category}: {count}")

print("MANIFEST:", manifest)
print("ORIGINAL DATASET: UNCHANGED")
print("NOTE: Review visual relevance and usage rights before training.")

