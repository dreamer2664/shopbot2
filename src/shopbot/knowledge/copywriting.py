"""Deterministic, rules-based listing copy generator.

No LLM, no cost: applies the researched SEO constraints (knowledge/__init__.py
sources [E][F]) mechanically so every generated title/meta/description passes
the same pre-publish checklist a human SEO would run. An LLM can be layered on
top later; the validators here would then grade the LLM output.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..providers.base import Design
from . import SEO, CATEGORIES


@dataclass
class ListingCopy:
    title: str
    meta_title: str
    meta_description: str
    alt_text: str
    description_html: str
    tags: list[str]
    warnings: list[str]


def _category_label(category: str) -> str:
    return CATEGORIES.get(category, CATEGORIES["tshirt"]).label.rstrip("s")


_TITLE_FILLERS = ["Original Art", "Printed on Demand", "Unique Gift Idea",
                  "Made to Order"]


def seo_title(design: Design, category: str = "tshirt") -> str:
    """50-80 chars, primary keyword (product type + main tag) front-loaded.

    Too-short titles are padded with benefit segments (never keyword spam);
    too-long titles are cut at a word boundary.
    """
    label = _category_label(category)
    primary = design.tags[0].replace("-", " ").strip() if design.tags else ""
    # avoid "T-shirt T-Shirt" when there is no tag to lead with
    if not primary or primary.lower() == label.lower():
        base = f"{label} - {design.title}".strip(" -")
    else:
        base = f"{primary.capitalize()} {label} - {design.title}".strip(" -")

    segments = [base]
    for filler in _TITLE_FILLERS:
        if len(" | ".join(segments)) >= SEO.title_min:
            break
        candidate = segments + [filler]
        if len(" | ".join(candidate)) > SEO.title_max:
            break
        segments = candidate
    title = " | ".join(segments)

    if len(title) > SEO.title_max:
        title = title[: SEO.title_max].rsplit(" ", 1)[0].rstrip(" -|,")
    return title


def meta_title(design: Design, category: str = "tshirt") -> str:
    t = seo_title(design, category)
    return t[: SEO.meta_title_max].rstrip(" -|,")


def meta_description(design: Design, category: str = "tshirt") -> str:
    """120-155 chars, keyword early, benefit + CTA at the end."""
    label = _category_label(category).lower()
    base = (f"{design.title} — original {label} design, printed on demand "
            f"and shipped to you. Premium quality, made to order.")
    if len(base) > SEO.meta_desc_max:
        base = base[: SEO.meta_desc_max - 1].rsplit(" ", 1)[0]
    while len(base) < SEO.meta_desc_min:
        pad = " Free EU shipping on eligible orders."
        if len(base) + len(pad) > SEO.meta_desc_max:
            base = (base + " Shop now.")[: SEO.meta_desc_max]
            break
        base += pad
    return base.rstrip()


def alt_text(design: Design, category: str = "tshirt") -> str:
    label = _category_label(category).lower()
    txt = f"{design.title} {label} with original artwork"
    if len(txt) > SEO.alt_text_max:
        txt = txt[: SEO.alt_text_max].rsplit(" ", 1)[0]
    while len(txt) < SEO.alt_text_min:
        txt += " art"
    return txt


def description_html(design: Design, category: str = "tshirt") -> str:
    """Simple structured description. Word-count target is for the *store*
    page overall; we generate a solid core and flag if it's thin."""
    label = _category_label(category)
    tags_sentence = ", ".join(design.tags) if design.tags else "original art"
    return (
        f"<h2>{design.title}</h2>"
        f"<p>{design.description}</p>"
        f"<p>This {label.lower()} features original artwork ({tags_sentence}), "
        f"printed on demand just for you — no mass warehouses, no waste. "
        f"Each item is produced only after you order it.</p>"
        f"<ul>"
        f"<li>Original design you won't find in chain stores</li>"
        f"<li>Printed and shipped within 2-5 business days</li>"
        f"<li>Care: wash cold inside-out, tumble dry low</li>"
        f"</ul>"
        f"<p>Questions about sizing or materials? Message us — we answer fast.</p>"
    )


def generate_copy(design: Design, category: str = "tshirt") -> ListingCopy:
    """Generate all listing fields and self-check against the SEO rules."""
    copy = ListingCopy(
        title=seo_title(design, category),
        meta_title=meta_title(design, category),
        meta_description=meta_description(design, category),
        alt_text=alt_text(design, category),
        description_html=description_html(design, category),
        tags=design.tags[:13],
        warnings=[],
    )
    # Self-validation (pre-publish checklist from source [E])
    if not (SEO.title_min <= len(copy.title) <= SEO.title_max):
        copy.warnings.append(f"title length {len(copy.title)} outside 50-80")
    kw = (design.tags[0].replace("-", " ") if design.tags else "").lower()
    if kw and kw not in copy.title[: SEO.keyword_front_chars].lower():
        copy.warnings.append(f"primary keyword '{kw}' not in first 30 chars")
    if len(copy.meta_title) > SEO.meta_title_max:
        copy.warnings.append("meta title too long")
    if not (SEO.meta_desc_min <= len(copy.meta_description) <= SEO.meta_desc_max):
        copy.warnings.append(
            f"meta description {len(copy.meta_description)} outside 120-155")
    if not (SEO.alt_text_min <= len(copy.alt_text) <= SEO.alt_text_max):
        copy.warnings.append("alt text outside 15-90")
    return copy
