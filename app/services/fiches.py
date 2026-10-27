"""Fiches pratiques — tutoriels téléchargeables d'une page, avec captures
d'écran, pour les usages les plus fréquents du portail (mêmes conventions
que app.services.member_access.build_guide_pdf : reportlab, A4)."""
import io
import os
from dataclasses import dataclass, field
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

TEAL = colors.HexColor("#2c7a7b")
GRAY = colors.HexColor("#6b7280")

STATIC_FICHES_DIR = os.path.join(os.path.dirname(__file__), "..", "static", "img", "fiches")


@dataclass
class FicheStep:
    number: int
    heading: str
    caption: str
    image_file: str          # chemin relatif à STATIC_FICHES_DIR
    image_width_cm: float     # largeur cible de l'image sur la page
    row: Optional[int] = None  # étapes partageant le même numéro de ligne → côte à côte


@dataclass
class Fiche:
    slug: str
    title: str
    intro: str
    steps: list[FicheStep] = field(default_factory=list)
    tip: Optional[str] = None


def _sized_image(path: str, target_width_cm: float) -> Image:
    from PIL import Image as PILImage
    with PILImage.open(path) as im:
        w_px, h_px = im.size
    width = target_width_cm * cm
    height = width * (h_px / w_px)
    img = Image(path, width=width, height=height)
    img.hAlign = "CENTER"
    return img


def build_fiche_pdf(fiche: Fiche, lodge_name: str) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=1.8 * cm, bottomMargin=1.8 * cm,
    )
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("H1", parent=styles["Heading1"], textColor=TEAL, fontSize=17, spaceAfter=2)
    subtitle = ParagraphStyle("Subtitle", parent=styles["Normal"], fontSize=10, textColor=GRAY, spaceAfter=10)
    intro = ParagraphStyle("Intro", parent=styles["Normal"], fontSize=10.5, leading=14, spaceAfter=12)
    step_heading = ParagraphStyle(
        "StepHeading", parent=styles["Normal"], fontName="Helvetica-Bold",
        fontSize=11.5, textColor=colors.HexColor("#1f2937"), spaceAfter=3,
    )
    step_caption = ParagraphStyle(
        "StepCaption", parent=styles["Normal"], fontSize=9.5, leading=13,
        textColor=colors.HexColor("#374151"), alignment=TA_LEFT,
    )
    tip_style = ParagraphStyle(
        "Tip", parent=styles["Normal"], fontSize=9.5, leading=13,
        textColor=colors.HexColor("#78350f"),
    )

    def badge(n: int):
        t = Table([[str(n)]], colWidths=[0.7 * cm], rowHeights=[0.7 * cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), TEAL),
            ("TEXTCOLOR", (0, 0), (-1, -1), colors.white),
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 11),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ROUNDEDCORNERS", [8, 8, 8, 8]),
        ]))
        return t

    story = [
        Paragraph(fiche.title, h1),
        Paragraph(f"Fiche pratique — {lodge_name}", subtitle),
        Paragraph(fiche.intro, intro),
    ]

    def step_block(step: "FicheStep") -> list:
        img_path = os.path.join(STATIC_FICHES_DIR, step.image_file)
        head_row = Table(
            [[badge(step.number), Paragraph(step.heading, step_heading)]],
            colWidths=[0.9 * cm, step.image_width_cm * cm + 2 * cm],
        )
        head_row.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ]))
        return [
            head_row, Spacer(1, 0.15 * cm),
            Paragraph(step.caption, step_caption), Spacer(1, 0.25 * cm),
            _sized_image(img_path, step.image_width_cm),
        ]

    # Groupe les étapes par numéro de ligne (row) : même row → côte à côte.
    rows: dict[int, list] = {}
    order: list[int] = []
    for i, step in enumerate(fiche.steps):
        key = step.row if step.row is not None else f"solo-{i}"
        if key not in rows:
            rows[key] = []
            order.append(key)
        rows[key].append(step)

    for key in order:
        steps_in_row = rows[key]
        if len(steps_in_row) == 1:
            story.extend(step_block(steps_in_row[0]))
        else:
            cols = [step_block(s) for s in steps_in_row]
            col_width = (17 / len(cols)) * cm
            grid = Table(
                [cols], colWidths=[col_width] * len(cols),
            )
            grid.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0.6 * cm),
            ]))
            story.append(grid)
        story.append(Spacer(1, 0.5 * cm))

    if fiche.tip:
        tip_table = Table(
            [[Paragraph(f"Astuce — {fiche.tip}", tip_style)]],
            colWidths=[17 * cm],
        )
        tip_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fffbeb")),
            ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#f59e0b")),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(tip_table)

    doc.build(story)
    return buf.getvalue()


# ── Catalogue des fiches disponibles ─────────────────────────────────────────

FICHES: dict[str, Fiche] = {
    "inscription-tenue": Fiche(
        slug="inscription-tenue",
        title="S'inscrire à une tenue et aux agapes",
        intro=(
            "En 3 étapes, depuis votre ordinateur ou votre téléphone : indiquez votre "
            "présence à une tenue et, si des agapes sont prévues, votre participation "
            "et votre régime alimentaire."
        ),
        steps=[
            FicheStep(
                1, "Ouvrez la tenue qui vous intéresse",
                "Rubrique <b>Tenues</b> dans le menu : cliquez sur la tenue à venir pour ouvrir sa fiche.",
                "inscription-tenue/step1.png", 16,
            ),
            FicheStep(
                2, "Indiquez votre présence et vos agapes",
                "Dans le bloc <b>Mon inscription</b> : choisissez « Je serai présent·e » ou "
                "« Je m'excuse », cochez « Je participe aux agapes » si besoin, et précisez "
                "votre régime alimentaire s'il est particulier.",
                "inscription-tenue/step2.png", 6.5, row=2,
            ),
            FicheStep(
                3, "Confirmez",
                "Cliquez sur le bouton en bas du bloc. Votre inscription est enregistrée dès "
                "que le bouton affiche « Mettre à jour » — vous pouvez revenir la modifier à "
                "tout moment avant la tenue.",
                "inscription-tenue/step3.png", 6.5, row=2,
            ),
        ],
        tip=(
            "Les inscriptions aux agapes ferment automatiquement la veille de la tenue à 8h — "
            "pensez à vous inscrire à l'avance."
        ),
    ),
}
