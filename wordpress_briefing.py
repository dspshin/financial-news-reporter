"""Prepare reviewed WordPress posts and journal connector actions after Kakao."""

import argparse
import fcntl
import json
import re
from datetime import datetime
from html import escape
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageChops

from image_delivery_edition import bundle_edition, validate_bundle
from publish_morning_image import ROOT, KST, save_receipt, sha256


STATE_DIR = ".wordpress_image_delivery"
INPUTS = ("briefing.png", "manuscript.txt", "sources.md", "source-checks.json",
          "qa.json", "blog-analysis.md", "blog-qa.json")
PHASES = {"prepared", "media_requested", "media_uploaded", "post_requested", "published", "deferred"}


def configuration(folder, root=ROOT):
    path = root / "WORDPRESS_DELIVERY.json"
    if not path.exists():
        return None
    config = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(config, dict) or not isinstance(config.get("site_id"), str)
            or not config["site_id"].isdigit() or int(config["site_id"]) <= 0
            or not isinstance(config.get("site_url"), str)
            or urlparse(config["site_url"]).scheme != "https"
            or not urlparse(config["site_url"]).netloc):
        raise ValueError("Invalid WordPress destination")
    cutoff = bundle_edition(Path(config["effective_from"]))
    return config if bundle_edition(folder) >= cutoff else None


def receipt_path(folder, root=ROOT):
    return root / STATE_DIR / (folder.name + ".json")


def category_id(config, edition):
    categories = config.get("category_ids", {})
    selected = categories.get(edition) if isinstance(categories, dict) else None
    if type(selected) is not int or selected <= 0:
        raise ValueError("Missing valid WordPress category for " + edition)
    return selected


def delivery_status(folder, root=ROOT):
    config = configuration(folder, root)
    if config is None:
        return {"status": "not_required"}
    path = receipt_path(folder, root)
    if not path.exists():
        return {"status": "not_started"}
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(record, dict) or not isinstance(record.get("input_sha256", {}), dict):
            raise ValueError("Invalid WordPress receipt structure")
        if (record.get("edition") != folder.name or record.get("site_id") != config["site_id"]
                or record.get("site_url") != config["site_url"]
                or record.get("image_sha256") != sha256(folder / "briefing.png")
                or record.get("status") not in PHASES):
            raise ValueError("WordPress receipt identity mismatch")
        if ("category_id" in record
                and record["category_id"] != category_id(config, bundle_edition(folder)[1])):
            raise ValueError("WordPress category changed after preparation")
        for name, digest in record.get("input_sha256", {}).items():
            if name not in INPUTS or sha256(folder / name) != digest:
                raise ValueError("WordPress inputs changed after preparation")
        if record["status"] != "deferred" and set(record.get("input_sha256", {})) != set(INPUTS):
            raise ValueError("WordPress source fingerprints are incomplete")
        if record["status"] in {"media_uploaded", "post_requested", "published"}:
            if record.get("payload_sha256") != sha256(folder / "wordpress-post.json"):
                raise ValueError("WordPress payload changed after preparation")
        if record["status"] == "published":
            if (type(record.get("post_id")) is not int or record["post_id"] <= 0
                    or not record.get("verified_at_kst")
                    or not record.get("content_verified") or not record.get("media_verified")
                    or not record.get("public_page_verified")
                    or urlparse(record.get("url", "")).netloc != urlparse(config["site_url"]).netloc):
                raise ValueError("Missing verified WordPress publication")
            if "category_id" in record and record.get("category_verified") is not True:
                raise ValueError("Missing verified WordPress category")
        if record["status"] == "deferred" and not record.get("evidence"):
            raise ValueError("Missing WordPress deferral evidence")
        return record
    except (OSError, ValueError, TypeError, KeyError):
        return {"status": "invalid", "receipt": path.name}


def inline(text):
    parts = []
    offset = 0
    for match in re.finditer(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", text):
        parts.extend((escape(text[offset:match.start()]),
                      '<a href="' + escape(match[2], quote=True) + '">' + escape(match[1]) + '</a>'))
        offset = match.end()
    parts.append(escape(text[offset:]))
    return "".join(parts)


def paragraph(text):
    return "<!-- wp:paragraph -->\n<p>" + text + "</p>\n<!-- /wp:paragraph -->"


def heading(text, level=2):
    attributes = "" if level == 2 else ' {"level":' + str(level) + "}"
    return (f'<!-- wp:heading{attributes} -->\n<h{level} class="wp-block-heading">'
            + escape(text) + f"</h{level}>\n<!-- /wp:heading -->")


def markdown_blocks(text):
    blocks = []
    for section in re.split(r"\n\s*\n", text.strip()):
        lines = section.splitlines()
        match = re.match(r"^(#{1,6})\s+(.+)$", lines[0])
        if match:
            blocks.append(heading(match[2], min(max(len(match[1]), 2), 4)))
            lines.pop(0)
        elif re.match(r"^[①②③④⑤⑥]|^3줄 요약$", lines[0]):
            blocks.append(heading(lines.pop(0), 3))
        if lines and all(line.startswith("- ") for line in lines):
            items = ["<!-- wp:list-item --><li>" + inline(line[2:])
                     + "</li><!-- /wp:list-item -->" for line in lines]
            blocks.append('<!-- wp:list -->\n<ul class="wp-block-list">' + "\n".join(items)
                          + "</ul>\n<!-- /wp:list -->")
        elif lines:
            blocks.append(paragraph("<br>".join(inline(line) for line in lines)))
    return blocks


def build_content(folder, config, media=None):
    day, edition = bundle_edition(folder)
    label = "아침" if edition == "am" else "마감"
    cutoff = "07:35" if edition == "am" else "16:20"
    blocks = [paragraph(f"<strong>정보 기준 {day.isoformat()} {cutoff} KST</strong>"),
              heading(f"{label} 시황 이미지")]
    if media:
        attrs = json.dumps({"id": media["id"], "sizeSlug": "full", "linkDestination": "media"})
        url = escape(media["source_url"], quote=True)
        blocks.append('<!-- wp:image ' + attrs + ' -->\n<figure class="wp-block-image size-full">'
                      '<a href="' + url + '"><img src="' + url + '" alt="'
                      + escape(f"{day.isoformat()} {label} 시황 인포그래픽", quote=True)
                      + '" class="wp-image-' + str(media["id"]) + '"/></a>'
                      '<figcaption class="wp-element-caption">' + f"{day.isoformat()} {label} 시황 · {cutoff} KST"
                      + "</figcaption></figure>\n<!-- /wp:image -->")
    blocks.append(heading("시황 원고 전문"))
    blocks.extend(markdown_blocks((folder / "manuscript.txt").read_text(encoding="utf-8")))
    blocks.append(heading("분석 근거와 이슈 선정 이유"))
    blocks.extend(markdown_blocks((folder / "blog-analysis.md").read_text(encoding="utf-8")))
    blocks.append(heading("출처와 관측시각"))
    blocks.extend(markdown_blocks((folder / "sources.md").read_text(encoding="utf-8")))
    return "\n\n".join(blocks) + "\n"


def prepare(folder, root, now):
    config = configuration(folder, root)
    if config is None:
        raise ValueError("WordPress is not enabled for this edition")
    current = delivery_status(folder, root)
    if current["status"] != "not_started":
        raise ValueError("Existing WordPress receipt: inspect status and resume without preparing again")
    validate_bundle(folder, now)
    from morning_delivery_status import reconcile
    channels = reconcile(folder, root, now, include_wordpress=False, persist=False)
    if not channels.get("all_targets_handled", channels["status"] == "completed"):
        raise ValueError("Process every Kakao target before WordPress")
    review = json.loads((folder / "blog-qa.json").read_text(encoding="utf-8"))
    reviewed_at = datetime.fromisoformat(review["checked_at_kst"])
    if (review.get("status") != "passed" or review.get("edition") != folder.name
            or not isinstance(review.get("checks"), dict) or not review["checks"]
            or any(value is not True for value in review["checks"].values())
            or reviewed_at.tzinfo is None or reviewed_at > now
            or reviewed_at < datetime.fromisoformat(json.loads((folder / "qa.json").read_text())["cutoff"])):
        raise ValueError("WordPress editorial review must pass after the cutoff")
    for name in INPUTS:
        if name != "blog-qa.json" and review.get("sha256", {}).get(name) != sha256(folder / name):
            raise ValueError("WordPress editorial input changed: " + name)
    analysis = (folder / "blog-analysis.md").read_text(encoding="utf-8").strip()
    if not analysis or not (folder / "sources.md").read_text(encoding="utf-8").strip():
        raise ValueError("WordPress analysis and sources are required")
    day, edition = bundle_edition(folder)
    label = "아침" if edition == "am" else "마감"
    selected_category = category_id(config, edition)
    lines = (folder / "manuscript.txt").read_text(encoding="utf-8").splitlines()
    title = f"{day.year}년 {day.month}월 {day.day}일 {label} 시황"
    if len(lines) > 1 and lines[1].strip():
        title += " — " + lines[1].strip()
    record = {"edition": folder.name, "site_id": config["site_id"], "site_url": config["site_url"],
              "status": "prepared", "category_id": selected_category,
              "image_sha256": sha256(folder / "briefing.png"),
              "input_sha256": {name: sha256(folder / name) for name in INPUTS},
              "slug": "market-" + folder.name, "title": title, "prepared_at_kst": now.isoformat()}
    upload = {"wpcom_site": config["site_id"], "file": str((folder / "briefing.png").resolve()),
              "filename": folder.name + "-briefing.png", "mime_type": "image/png",
              "title": title, "alt_text": f"{day.isoformat()} {label} 시황 인포그래픽"}
    save_receipt(folder / "wordpress-upload.json", upload)
    save_receipt(receipt_path(folder, root), record)
    return record


def media_uploaded(folder, root, record, data):
    media = data.get("data", data)
    if not isinstance(media, dict):
        raise ValueError("Missing WordPress media response")
    config = configuration(folder, root)
    if (type(media.get("id")) is not int or media["id"] <= 0 or media.get("mime_type") != "image/png"
            or urlparse(media.get("source_url", "")).scheme != "https"
            or urlparse(media["source_url"]).netloc != urlparse(config["site_url"]).netloc):
        raise ValueError("Unexpected WordPress media identity or host")
    response = requests.get(media["source_url"], timeout=(10, 30), allow_redirects=False)
    response.raise_for_status()
    original = Image.open(folder / "briefing.png").convert("RGB")
    uploaded = Image.open(BytesIO(response.content)).convert("RGB")
    if original.size != uploaded.size or ImageChops.difference(original, uploaded).getbbox() is not None:
        raise ValueError("Uploaded WordPress image differs from the reviewed image")
    content = build_content(folder, config, media)
    payload = {"wpcom_site": config["site_id"], "title": record["title"], "slug": record["slug"],
               "content": content, "featured_media": media["id"], "status": "publish",
               "categories": [record["category_id"]],
               "meta": {"_jetpack_dont_email_post_to_subs": True, "jetpack_publicize_feature_enabled": False}}
    save_receipt(folder / "wordpress-post.json", payload)
    record.update(status="media_uploaded", media_id=media["id"], media_url=media["source_url"], media_verified=True,
                  payload_sha256=sha256(folder / "wordpress-post.json"))


def signature(content):
    document = BeautifulSoup(content, "html.parser")
    return (" ".join(document.stripped_strings), [(a.get("href")) for a in document.select("a[href]")],
            [(a.get("src"), a.get("alt")) for a in document.select("img")],
            [(a.name, a.get_text()) for a in document.select("h2,h3,h4")],
            re.findall(r"<!--\s*(/?wp:[\w/-]+)", content))


def published(folder, record, data, now):
    post = data.get("data", data)
    if not isinstance(post, dict):
        raise ValueError("Missing WordPress post read-back")
    payload = json.loads((folder / "wordpress-post.json").read_text(encoding="utf-8"))
    content = post.get("content", "")
    if isinstance(content, dict):
        content = content.get("raw", "")
    if "categories" in payload and post.get("categories") != payload["categories"]:
        raise ValueError("WordPress read-back category does not match the prepared edition")
    if (post.get("status") != "publish" or type(post.get("id")) is not int or post["id"] <= 0
            or post.get("slug") != record["slug"] or post.get("featured_media") != record["media_id"]
            or urlparse(post.get("link", "")).netloc != urlparse(record["site_url"]).netloc
            or signature(content) != signature(payload["content"])
            or post.get("meta", {}).get("_jetpack_dont_email_post_to_subs") is not True
            or post.get("meta", {}).get("jetpack_publicize_feature_enabled") is not False):
        raise ValueError("WordPress read-back does not match the prepared public post")
    response = requests.get(post["link"], timeout=(10, 30), allow_redirects=False)
    response.raise_for_status()
    visible = " ".join(BeautifulSoup(response.content, "html.parser").stripped_strings)
    for line in (folder / "manuscript.txt").read_text(encoding="utf-8").splitlines():
        if line.strip() and " ".join(line.split()) not in visible:
            raise ValueError("Public WordPress page is missing manuscript content")
    record.update(status="published", post_id=post["id"], url=post["link"], content_verified=True,
                  public_page_verified=True, verified_at_kst=now.isoformat())
    if "categories" in payload:
        record["category_verified"] = True


def perform(action, folder, root=ROOT, now=None, result=None, evidence=None):
    now = now or datetime.now(KST)
    state_root = root / STATE_DIR
    state_root.mkdir(exist_ok=True)
    with (state_root / (folder.name + ".lock")).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if action == "prepare":
            return prepare(folder, root, now)
        record = delivery_status(folder, root)
        if action == "status":
            return record
        if record["status"] in {"not_required", "invalid", "published"}:
            raise ValueError("WordPress receipt is terminal or invalid; inspect without writing")
        if action == "retry":
            if not evidence or record["status"] == "not_started":
                raise ValueError("Read retry needs an existing request and actual error evidence")
            phase = record.get("previous_status") if record["status"] == "deferred" else record["status"]
            attempts = record.setdefault("read_retries", {}).setdefault(phase, [])
            if len(attempts) >= 3:
                raise ValueError("WordPress read retry limit reached; preserve the receipt and defer")
            attempts.append({"at_kst": now.isoformat(), "evidence": evidence})
        elif action == "defer":
            if not evidence:
                raise ValueError("WordPress deferral needs actual error and resume evidence")
            if record["status"] == "deferred":
                return record
            if record["status"] == "not_started":
                config = configuration(folder, root)
                record.update(edition=folder.name, site_id=config["site_id"], site_url=config["site_url"],
                              image_sha256=sha256(folder / "briefing.png"))
            record.update(previous_status=record["status"], status="deferred", evidence=evidence,
                          deferred_at_kst=now.isoformat())
        else:
            allowed = {"begin-media": {"prepared"}, "media": {"media_requested"},
                       "begin-post": {"media_uploaded"}, "published": {"post_requested"}}
            current = record.get("previous_status") if record["status"] == "deferred" else record["status"]
            if current not in allowed[action]:
                raise ValueError("Invalid WordPress phase transition; do not repeat connector writes")
            if record["status"] == "deferred" and action in {"begin-media", "begin-post"}:
                raise ValueError("Deferred connector writes require inspection, not automatic retry")
            if action == "begin-media":
                record.update(status="media_requested", media_requested_at_kst=now.isoformat())
            elif action == "media":
                media_uploaded(folder, root, record, result)
            elif action == "begin-post":
                if sha256(folder / "wordpress-post.json") != record["payload_sha256"]:
                    raise ValueError("Prepared WordPress payload changed")
                record.update(status="post_requested", post_requested_at_kst=now.isoformat())
            else:
                published(folder, record, result, now)
        save_receipt(receipt_path(folder, root), record)
        return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "status", "begin-media", "media", "begin-post", "published", "retry", "defer"))
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--evidence")
    args = parser.parse_args()
    result = json.loads(args.result.read_text(encoding="utf-8")) if args.result else None
    if args.action in {"media", "published"} and not isinstance(result, dict):
        parser.error("--result must contain the actual WordPress connector response")
    record = perform(args.action, args.bundle.resolve(), result=result, evidence=args.evidence)
    from morning_delivery_status import reconcile
    reconcile(args.bundle.resolve())
    print(json.dumps(record, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, TypeError, KeyError, requests.RequestException) as error:
        print(json.dumps({"status": "error", "error_type": type(error).__name__,
                          "message": str(error) if isinstance(error, ValueError)
                          else "WordPress operation failed; inspect the receipt and inputs before retrying"}))
        raise SystemExit(2)
