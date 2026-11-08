"""Export PDF du bilan comptable (charges/produits par catégorie, résultat,
cotisations, budget vs réalisé) — même contenu que la page HTML
app/templates/pages/finance/bilan.html, mis en forme pour impression/archivage."""
import io
from datetime import date
from decimal import Decimal
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

TEAL = colors.HexColor("#2c7a7b")
GRAY = colors.HexColor("#6b7280")
GREEN_BG = colors.HexColor("#f0fdf4")
GREEN_TXT = colors.HexColor("#15803d")
RED_BG = colors.HexColor("#fef2f2")
RED_TXT = colors.HexColor("#b91c1c")
AMBER_BG = colors.HexColor("#fffbeb")
AMBER_BORDER = colors.HexColor("#f59e0b")
AMBER_TXT = colors.HexColor("#92400e")


def _eur(amount) -> str:
    return f"{float(amount):,.2f} €".replace(",", " ").replace(".", ",")


def build_bilan_pdf(
    lodge_name: str,
    year_label: str,
    data: dict,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=1.8 * cm, rightMargin=1.8 * cm,
        topMargin=1.8 * cm, bottomMargin=1.8 * cm,
    )
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("H1", parent=styles["Heading1"], textColor=TEAL, fontSize=18, spaceAfter=2)
    subtitle = ParagraphStyle("Subtitle", parent=styles["Normal"], fontSize=10.5, textColor=GRAY, spaceAfter=14)
    section_title = ParagraphStyle(
        "SectionTitle", parent=styles["Normal"], fontName="Helvetica-Bold",
        fontSize=11, textColor=colors.HexColor("#1f2937"),
    )
    cat_label = ParagraphStyle("CatLabel", parent=styles["Normal"], fontSize=9.5, textColor=colors.HexColor("#374151"))
    cat_amount = ParagraphStyle("CatAmount", parent=styles["Normal"], fontSize=9.5, alignment=TA_RIGHT, fontName="Helvetica-Bold")
    note_style = ParagraphStyle("Note", parent=styles["Normal"], fontSize=9, textColor=AMBER_TXT, leading=12)
    card_label = ParagraphStyle("CardLabel", parent=styles["Normal"], fontSize=8.5, alignment=TA_CENTER)
    card_amount = ParagraphStyle("CardAmount", parent=styles["Normal"], fontSize=15, alignment=TA_CENTER, fontName="Helvetica-Bold")
    card_sub = ParagraphStyle("CardSub", parent=styles["Normal"], fontSize=7.5, alignment=TA_CENTER)

    period_label = "Année complète"
    if date_from or date_to:
        period_label = f"Du {date_from.strftime('%d/%m/%Y') if date_from else '…'} au {date_to.strftime('%d/%m/%Y') if date_to else '…'}"

    story = [
        Paragraph("Bilan comptable", h1),
        Paragraph(f"{lodge_name} — {year_label} · {period_label}", subtitle),
    ]

    # ── Alerte capitation ──────────────────────────────────────────────────
    cap_due = data.get("total_capitation_encaissee", Decimal("0"))
    if cap_due and cap_due > 0:
        cfg = data.get("contrib_cfg")
        nat = float(cfg.national_capitation_rate) if cfg else 0
        reg = float(cfg.regional_capitation_rate) if cfg else 0
        note = Table(
            [[Paragraph(
                f"<b>Capitation à reverser :</b> {_eur(cap_due)} (nationale {nat:.2f} € + "
                f"régionale {reg:.2f} € × membres Appartenance). Collectée pour le compte de "
                f"l'obédience — déduite du résultat net ci-dessous.",
                note_style,
            )]],
            colWidths=[17.4 * cm],
        )
        note.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), AMBER_BG),
            ("BOX", (0, 0), (-1, -1), 1, AMBER_BORDER),
            ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ]))
        story += [note, Spacer(1, 0.4 * cm)]

    # ── 3 cartes résumé ────────────────────────────────────────────────────
    resultat = data["resultat"]
    resultat_txt = f"{'+ ' if resultat >= 0 else '− '}{_eur(abs(resultat))}"
    resultat_label = "Excédent net" if resultat >= 0 else "Déficit net"
    resultat_bg = GREEN_BG if resultat >= 0 else colors.HexColor("#fff7ed")
    resultat_txt_color = GREEN_TXT if resultat >= 0 else colors.HexColor("#c2410c")

    def card(bg, border, label_color, label, amount, amount_color, sub):
        t = Table(
            [[Paragraph(label, ParagraphStyle("l", parent=card_label, textColor=label_color))],
             [Paragraph(amount, ParagraphStyle("a", parent=card_amount, textColor=amount_color))],
             [Paragraph(sub, ParagraphStyle("s", parent=card_sub, textColor=label_color))]],
            colWidths=[5.6 * cm],
        )
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), bg),
            ("BOX", (0, 0), (-1, -1), 1, border),
            ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ]))
        return t

    cards = Table(
        [[
            card(GREEN_BG, colors.HexColor("#bbf7d0"), GREEN_TXT, "TOTAL PRODUITS BRUTS",
                 _eur(data["total_produits_global"]), GREEN_TXT,
                 f"dont {_eur(data['total_cotisations_nettes'])} cot. nettes"),
            card(RED_BG, colors.HexColor("#fecaca"), RED_TXT, "TOTAL CHARGES",
                 _eur(data["total_charges"]), RED_TXT,
                 f"Budget prévu : {_eur(data['total_budget'])}"),
            card(resultat_bg, colors.HexColor("#fed7aa") if resultat < 0 else colors.HexColor("#99f6e4"),
                 resultat_txt_color, resultat_label, resultat_txt, resultat_txt_color,
                 "Cot. nettes + produits − charges"),
        ]],
        colWidths=[5.8 * cm, 5.8 * cm, 5.8 * cm],
    )
    cards.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4)]))
    story += [cards, Spacer(1, 0.5 * cm)]

    # ── CHARGES / PRODUITS deux colonnes ───────────────────────────────────
    def section_header(title, total, bg, txt_color):
        title_style = ParagraphStyle("sh", parent=section_title, textColor=txt_color)
        amount_style = ParagraphStyle("sha", parent=title_style, alignment=TA_RIGHT)
        t = Table([[Paragraph(title, title_style), Paragraph(_eur(total), amount_style)]],
                   colWidths=[5.6 * cm, 2.9 * cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), bg),
            ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (0, 0), 8), ("RIGHTPADDING", (-1, -1), (-1, -1), 8),
        ]))
        return t

    def make_cat_table(items, empty_msg):
        if items:
            rows = [[Paragraph(c, cat_label), Paragraph(_eur(a), cat_amount)] for c, a in items.items()]
            widths = [5.6 * cm, 2.9 * cm]
        else:
            rows = [[Paragraph(empty_msg, ParagraphStyle("e", parent=cat_label, textColor=GRAY))]]
            widths = [8.5 * cm]
        t = Table(rows, colWidths=widths)
        t.setStyle(TableStyle([
            ("LINEBELOW", (0, 0), (-1, -2), 0.4, colors.HexColor("#f3f4f6")),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (0, -1), 8), ("RIGHTPADDING", (-1, 0), (-1, -1), 8),
        ]))
        return t

    def footer_row(label, total, bg, txt_color):
        t = Table([[Paragraph(label, ParagraphStyle("fl", parent=cat_label, fontName="Helvetica-Bold", textColor=txt_color)),
                     Paragraph(_eur(total), ParagraphStyle("fa", parent=cat_amount, textColor=txt_color))]],
                   colWidths=[5.6 * cm, 2.9 * cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), bg),
            ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (0, 0), 8), ("RIGHTPADDING", (-1, -1), (-1, -1), 8),
        ]))
        return t

    charges_col = [
        section_header("CHARGES", data["total_charges"], RED_BG, RED_TXT),
        make_cat_table(data["charges_by_cat"], "Aucune dépense enregistrée"),
        footer_row("Total charges", data["total_charges"], RED_BG, RED_TXT),
    ]

    # Produits : cotisations en ligne spéciale, puis autres catégories
    produits_items = {}
    produits_items["Cotisations brutes encaissées"] = data["total_cotisations"]
    if data["total_capitation_encaissee"] > 0:
        produits_items["− Capitation à reverser"] = -data["total_capitation_encaissee"]
        produits_items["Cotisations nettes (part loge)"] = data["total_cotisations_nettes"]
    produits_items.update(data["produits_by_cat"])

    produits_col = [
        section_header("PRODUITS", data["total_produits_global"], GREEN_BG, GREEN_TXT),
        make_cat_table(produits_items, "Aucune recette enregistrée"),
        footer_row("Total produits", data["total_produits_global"], GREEN_BG, GREEN_TXT),
    ]

    two_col = Table([[charges_col, produits_col]], colWidths=[8.7 * cm, 8.7 * cm])
    two_col.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (0, 0), 0.3 * cm),
        ("BOX", (0, 0), (0, 0), 0.75, colors.HexColor("#f3f4f6")),
        ("BOX", (1, 0), (1, 0), 0.75, colors.HexColor("#f3f4f6")),
    ]))
    story += [two_col, Spacer(1, 0.5 * cm)]

    # ── Résultat net encadré ───────────────────────────────────────────────
    resultat_box = Table(
        [[Paragraph(
            f"<b>{resultat_label} de l'exercice</b><br/>"
            f"<font size=8 color='#6b7280'>Total Produits ({_eur(data['total_produits_global'])}) "
            f"− Total Charges ({_eur(data['total_charges'])})</font>",
            ParagraphStyle("rb", parent=styles["Normal"], fontSize=11, textColor=resultat_txt_color, leading=14),
        ), Paragraph(f"{resultat_txt}", ParagraphStyle(
            "rbamt", parent=styles["Normal"], fontSize=16, fontName="Helvetica-Bold",
            alignment=TA_RIGHT, textColor=resultat_txt_color,
        ))]],
        colWidths=[12 * cm, 5.4 * cm],
    )
    resultat_box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), resultat_bg),
        ("BOX", (0, 0), (-1, -1), 1.3, colors.HexColor("#fdba74") if resultat < 0 else colors.HexColor("#5eead4")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (0, 0), 10), ("RIGHTPADDING", (-1, -1), (-1, -1), 10),
    ]))
    story += [resultat_box, Spacer(1, 0.5 * cm)]

    # ── Situation des cotisations ───────────────────────────────────────────
    creances = data["creances"]
    cot_rows = [
        [Paragraph("Cotisations appelées (total dû)", cat_label), Paragraph(_eur(data["total_cotisations_due"]), cat_amount)],
        [Paragraph("Cotisations encaissées", cat_label), Paragraph(_eur(data["total_cotisations"]), ParagraphStyle("g", parent=cat_amount, textColor=GREEN_TXT))],
        [Paragraph("Créances (impayés)", ParagraphStyle("cl", parent=cat_label, fontName="Helvetica-Bold")),
         Paragraph(_eur(creances), ParagraphStyle("ca", parent=cat_amount, textColor=(AMBER_TXT if creances > 0 else GREEN_TXT)))],
    ]
    cot_header_style = ParagraphStyle("coth", parent=section_title, textColor=colors.white)
    cot_table = Table(
        [[Paragraph("Situation des cotisations", cot_header_style)]] + [[r[0], r[1]] for r in cot_rows],
        colWidths=[14.8 * cm, 2.6 * cm],
    )
    cot_table.setStyle(TableStyle([
        ("SPAN", (0, 0), (-1, 0)),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4b5563")),
        ("BACKGROUND", (0, 3), (-1, 3), AMBER_BG if creances > 0 else colors.HexColor("#f9fafb")),
        ("LINEBELOW", (0, 1), (-1, 2), 0.4, colors.HexColor("#f3f4f6")),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (0, -1), 8), ("RIGHTPADDING", (-1, 0), (-1, -1), 8),
    ]))
    story += [cot_table, Spacer(1, 0.5 * cm)]

    # ── Budget vs Réalisé ───────────────────────────────────────────────────
    if data["budget_by_cat"]:
        all_cats = sorted(set(list(data["budget_by_cat"].keys()) + list(data["charges_by_cat"].keys())))
        th = ParagraphStyle("th", parent=styles["Normal"], fontSize=8.5, textColor=GRAY, fontName="Helvetica-Bold")
        td = ParagraphStyle("td", parent=styles["Normal"], fontSize=9)
        td_r = ParagraphStyle("tdr", parent=td, alignment=TA_RIGHT)

        rows = [[
            Paragraph("Catégorie", th), Paragraph("Prévu", ParagraphStyle("thr", parent=th, alignment=TA_RIGHT)),
            Paragraph("Réalisé", ParagraphStyle("thr2", parent=th, alignment=TA_RIGHT)),
            Paragraph("Écart", ParagraphStyle("thr3", parent=th, alignment=TA_RIGHT)),
        ]]
        total_prevu = data["total_budget"]
        total_realise = data["total_charges"]
        for cat_name in all_cats:
            prevu = data["budget_by_cat"].get(cat_name, Decimal("0"))
            realise = data["charges_by_cat"].get(cat_name, Decimal("0"))
            ecart = prevu - realise
            ecart_color = RED_TXT if ecart < 0 else (GREEN_TXT if ecart > 0 else GRAY)
            rows.append([
                Paragraph(cat_name, td), Paragraph(_eur(prevu), td_r), Paragraph(_eur(realise), td_r),
                Paragraph(f"{'+' if ecart > 0 else ''}{_eur(ecart)}", ParagraphStyle("ec", parent=td_r, textColor=ecart_color)),
            ])
        ecart_total = total_prevu - total_realise
        ecart_total_color = RED_TXT if ecart_total < 0 else (GREEN_TXT if ecart_total > 0 else GRAY)
        rows.append([
            Paragraph("Total", ParagraphStyle("tl", parent=td, fontName="Helvetica-Bold")),
            Paragraph(_eur(total_prevu), ParagraphStyle("tp", parent=td_r, fontName="Helvetica-Bold")),
            Paragraph(_eur(total_realise), ParagraphStyle("tr2", parent=td_r, fontName="Helvetica-Bold")),
            Paragraph(f"{'+' if ecart_total > 0 else ''}{_eur(ecart_total)}",
                      ParagraphStyle("tec", parent=td_r, fontName="Helvetica-Bold", textColor=ecart_total_color)),
        ])

        story.append(Paragraph("Budget prévisionnel vs Réalisé", ParagraphStyle(
            "bh", parent=styles["Normal"], fontSize=11, fontName="Helvetica-Bold",
            textColor=colors.HexColor("#1f2937"), spaceAfter=6,
        )))
        budget_table = Table(rows, colWidths=[7.2 * cm, 3.4 * cm, 3.4 * cm, 3.4 * cm])
        budget_table.setStyle(TableStyle([
            ("LINEBELOW", (0, 0), (-1, 0), 0.75, colors.HexColor("#e5e7eb")),
            ("LINEBELOW", (0, 1), (-1, -2), 0.4, colors.HexColor("#f3f4f6")),
            ("LINEABOVE", (0, -1), (-1, -1), 1, colors.HexColor("#9ca3af")),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(budget_table)

    doc.build(story)
    return buf.getvalue()
