"""Export PDF du bilan comptable (charges/produits par catégorie, résultat
brut et net, capitation nationale/régionale, cotisations, budget vs réalisé)
— présentation détaillée façon bilan comptable, pour impression/archivage."""
import io
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

TEAL = colors.HexColor("#2c7a7b")
GRAY = colors.HexColor("#6b7280")
DARK = colors.HexColor("#1f2937")
GREEN_BG = colors.HexColor("#f0fdf4")
GREEN_TXT = colors.HexColor("#15803d")
RED_BG = colors.HexColor("#fef2f2")
RED_TXT = colors.HexColor("#b91c1c")
ORANGE_BG = colors.HexColor("#fff7ed")
ORANGE_TXT = colors.HexColor("#c2410c")
AMBER_BG = colors.HexColor("#fffbeb")
AMBER_BORDER = colors.HexColor("#f59e0b")
AMBER_TXT = colors.HexColor("#92400e")
SLATE_BG = colors.HexColor("#4b5563")
INDIGO_BG = colors.HexColor("#eef2ff")
INDIGO_TXT = colors.HexColor("#4338ca")
INDIGO_BORDER = colors.HexColor("#c7d2fe")


def _eur(amount) -> str:
    sign = "-" if float(amount) < 0 else ""
    return f"{sign}{abs(float(amount)):,.2f} €".replace(",", " ").replace(".", ",")


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
    subtitle = ParagraphStyle("Subtitle", parent=styles["Normal"], fontSize=10.5, textColor=GRAY, spaceAfter=6)
    intro_style = ParagraphStyle("Intro", parent=styles["Normal"], fontSize=8.5, textColor=GRAY, leading=12, spaceAfter=14)
    section_title = ParagraphStyle(
        "SectionTitle", parent=styles["Normal"], fontName="Helvetica-Bold",
        fontSize=11, textColor=DARK,
    )
    cat_label = ParagraphStyle("CatLabel", parent=styles["Normal"], fontSize=9.5, textColor=colors.HexColor("#374151"))
    cat_amount = ParagraphStyle("CatAmount", parent=styles["Normal"], fontSize=9.5, alignment=TA_RIGHT, fontName="Helvetica-Bold")
    note_style = ParagraphStyle("Note", parent=styles["Normal"], fontSize=8.5, textColor=GRAY, leading=12)
    card_label = ParagraphStyle("CardLabel", parent=styles["Normal"], fontSize=7.5, alignment=TA_CENTER)
    card_amount = ParagraphStyle("CardAmount", parent=styles["Normal"], fontSize=13, alignment=TA_CENTER, fontName="Helvetica-Bold")
    card_sub = ParagraphStyle("CardSub", parent=styles["Normal"], fontSize=6.8, alignment=TA_CENTER)

    period_label = "Année complète"
    if date_from or date_to:
        period_label = f"Du {date_from.strftime('%d/%m/%Y') if date_from else '…'} au {date_to.strftime('%d/%m/%Y') if date_to else '…'}"

    story = [
        Paragraph("Bilan comptable", h1),
        Paragraph(f"{lodge_name} — {year_label} · {period_label}", subtitle),
        Paragraph(
            "Compte de résultat de l'exercice : charges et produits détaillés par catégorie, "
            "résultat brut et net (capitation déduite), situation des cotisations et, le cas "
            "échéant, comparaison avec le budget voté.",
            intro_style,
        ),
    ]

    # ── Calculs capitation nationale / régionale (au prorata du taux) ──────
    cfg = data.get("contrib_cfg")
    nat_rate = float(cfg.national_capitation_rate) if cfg else 0.0
    reg_rate = float(cfg.regional_capitation_rate) if cfg else 0.0
    rate_total = nat_rate + reg_rate
    cap_due_total = float(data.get("total_capitation_due") or 0)
    cap_enc_total = float(data.get("total_capitation_encaissee") or 0)
    if rate_total > 0:
        nat_ratio = nat_rate / rate_total
        reg_ratio = reg_rate / rate_total
    else:
        nat_ratio = reg_ratio = 0.0
    cap_nat_due = cap_due_total * nat_ratio
    cap_reg_due = cap_due_total * reg_ratio
    cap_nat_enc = cap_enc_total * nat_ratio
    cap_reg_enc = cap_enc_total * reg_ratio

    # ── 4 cartes résumé : produits bruts / charges / résultat brut / net ───
    resultat_net = data["resultat"]
    resultat_brut = data["resultat_brut"]

    def fmt_signed(v):
        return f"{'+ ' if v >= 0 else '− '}{_eur(abs(v))}"

    def card(bg, border, label_color, label, amount, amount_color, sub, width=4.2 * cm):
        t = Table(
            [[Paragraph(label, ParagraphStyle("l", parent=card_label, textColor=label_color))],
             [Paragraph(amount, ParagraphStyle("a", parent=card_amount, textColor=amount_color))],
             [Paragraph(sub, ParagraphStyle("s", parent=card_sub, textColor=label_color))]],
            colWidths=[width],
        )
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), bg),
            ("BOX", (0, 0), (-1, -1), 1, border),
            ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ]))
        return t

    brut_color = GREEN_TXT if resultat_brut >= 0 else ORANGE_TXT
    net_color = GREEN_TXT if resultat_net >= 0 else ORANGE_TXT

    cards = Table(
        [[
            card(GREEN_BG, colors.HexColor("#bbf7d0"), GREEN_TXT, "PRODUITS BRUTS",
                 _eur(data["total_produits_global"]), GREEN_TXT,
                 f"dont {_eur(data['total_cotisations'])} cotisations"),
            card(RED_BG, colors.HexColor("#fecaca"), RED_TXT, "CHARGES",
                 _eur(data["total_charges"]), RED_TXT,
                 f"Budget prévu {_eur(data['total_budget'])}"),
            card(GREEN_BG if resultat_brut >= 0 else ORANGE_BG,
                 colors.HexColor("#bbf7d0") if resultat_brut >= 0 else colors.HexColor("#fed7aa"),
                 brut_color, "RÉSULTAT BRUT", fmt_signed(resultat_brut), brut_color,
                 "Capitation incluse"),
            card(INDIGO_BG, INDIGO_BORDER, INDIGO_TXT, "RÉSULTAT NET", fmt_signed(resultat_net), net_color,
                 "Capitation exclue — part loge"),
        ]],
        colWidths=[4.35 * cm, 4.35 * cm, 4.35 * cm, 4.35 * cm],
    )
    cards.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]))
    story += [cards, Spacer(1, 0.3 * cm)]

    # ── Synthèse : reste à encaisser / reste à payer ────────────────────────
    # Même synthèse que le haut de la page Bilan : où on en est à date.
    reste_a_payer = float(data.get("reste_a_payer_budget") or 0)
    reste_a_encaisser = float(data.get("reste_a_encaisser_total") or 0)
    depassement = float(data.get("depassements_budget") or 0)
    GRAY_BG = colors.HexColor("#f9fafb")
    GRAY_BORDER = colors.HexColor("#e5e7eb")
    payer_sub = "Lignes de budget encore ouvertes (non clôturées)"
    if depassement > 0.5:
        payer_sub += f" · {_eur(depassement)} de dépassement par ailleurs"
    synth_cards = Table(
        [[
            card(GRAY_BG, GRAY_BORDER, GRAY, "RESTE À ENCAISSER", _eur(reste_a_encaisser), DARK,
                 "Cotisations dues (capitation comprise) + recettes prévisionnelles non versées", width=8.5 * cm),
            card(GRAY_BG, GRAY_BORDER, GRAY, "RESTE À PAYER", _eur(reste_a_payer), DARK, payer_sub, width=8.5 * cm),
        ]],
        colWidths=[8.7 * cm, 8.7 * cm],
    )
    synth_cards.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]))
    story += [synth_cards, Spacer(1, 0.5 * cm)]

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

    # Produits : cotisations brutes en ligne spéciale, puis les autres catégories
    # (le détail capitation nationale/régionale et cotisations nettes est
    # présenté séparément plus bas, dans sa propre section).
    produits_items = {"Cotisations brutes encaissées": data["total_cotisations"]}
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

    # ── Résultat brut et net, détaillés et expliqués ───────────────────────
    resultat_rows = [
        [Paragraph("Résultat BRUT de l'exercice", ParagraphStyle("rb1", parent=section_title, fontSize=10, textColor=brut_color)),
         Paragraph(fmt_signed(resultat_brut), ParagraphStyle("rb1a", parent=cat_amount, fontSize=12, textColor=brut_color))],
        [Paragraph(
            f"Total produits ({_eur(data['total_produits_global'])}) − total charges ({_eur(data['total_charges'])}). "
            f"Inclut la capitation collectée pour l'obédience, qui ne reste pas à la loge.",
            note_style,
        ), ""],
        [Paragraph("Résultat NET de l'exercice (part réelle de la loge)",
                    ParagraphStyle("rb2", parent=section_title, fontSize=10, textColor=net_color)),
         Paragraph(fmt_signed(resultat_net), ParagraphStyle("rb2a", parent=cat_amount, fontSize=12, textColor=net_color))],
        [Paragraph(
            f"Résultat brut ({fmt_signed(resultat_brut)}) − capitation à reverser ({_eur(cap_enc_total)}). "
            f"C'est ce résultat net qui reflète la trésorerie réellement disponible pour la loge.",
            note_style,
        ), ""],
    ]
    resultat_table = Table(resultat_rows, colWidths=[14.8 * cm, 2.6 * cm])
    resultat_table.setStyle(TableStyle([
        ("SPAN", (0, 1), (-1, 1)), ("SPAN", (0, 3), (-1, 3)),
        ("BACKGROUND", (0, 0), (-1, 1), GREEN_BG if resultat_brut >= 0 else ORANGE_BG),
        ("BACKGROUND", (0, 2), (-1, 3), INDIGO_BG),
        ("TOPPADDING", (0, 0), (-1, 0), 8), ("BOTTOMPADDING", (0, 1), (-1, 1), 8),
        ("TOPPADDING", (0, 2), (-1, 2), 8), ("BOTTOMPADDING", (0, 3), (-1, 3), 8),
        ("TOPPADDING", (0, 1), (-1, 1), 0), ("TOPPADDING", (0, 3), (-1, 3), 0),
        ("LEFTPADDING", (0, 0), (0, -1), 10), ("RIGHTPADDING", (-1, 0), (-1, -1), 10),
        ("BOX", (0, 0), (-1, 1), 1, colors.HexColor("#bbf7d0") if resultat_brut >= 0 else colors.HexColor("#fed7aa")),
        ("BOX", (0, 2), (-1, 3), 1, INDIGO_BORDER),
    ]))
    story += [resultat_table, Spacer(1, 0.5 * cm)]

    # ── Capitation collectée pour l'obédience (nationale / régionale) ──────
    cap_header_style = ParagraphStyle("caph", parent=section_title, textColor=colors.white)
    def _reconcile_row(label, paid_key, restant_key, closed=False):
        paid = float(data.get(paid_key) or 0)
        restant = float(data.get(restant_key) or 0)
        if closed:
            restant_display = "✓ Soldée"
            color = GREEN_TXT
        else:
            restant_display = _eur(restant)
            color = RED_TXT if restant > 0.005 else GREEN_TXT
        return [
            Paragraph(label, ParagraphStyle("capr", parent=cat_label, fontName="Helvetica-Oblique", fontSize=8.5)),
            "",
            Paragraph(_eur(paid), ParagraphStyle("caprp", parent=cat_amount, fontSize=8.5)),
            Paragraph(restant_display, ParagraphStyle("caprr", parent=cat_amount, fontSize=8.5, textColor=color)),
        ]

    nat_closed = bool(cfg and cfg.capitation_nationale_closed)
    reg_closed = bool(cfg and cfg.capitation_regionale_closed)

    cap_rows = [
        [Paragraph("Capitation collectée pour l'obédience", cap_header_style), "", "", ""],
        [Paragraph("", cat_label),
         Paragraph("Taux unitaire", ParagraphStyle("cth", parent=cat_label, fontName="Helvetica-Bold", alignment=TA_RIGHT)),
         Paragraph("Due", ParagraphStyle("cth2", parent=cat_label, fontName="Helvetica-Bold", alignment=TA_RIGHT)),
         Paragraph("Encaissée", ParagraphStyle("cth3", parent=cat_label, fontName="Helvetica-Bold", alignment=TA_RIGHT))],
        [Paragraph("Capitation nationale (GODF)", cat_label), Paragraph(_eur(nat_rate), cat_amount),
         Paragraph(_eur(cap_nat_due), cat_amount), Paragraph(_eur(cap_nat_enc), cat_amount)],
        _reconcile_row("↳ effectivement reversé / reste (GODF)", "total_capitation_paid_nationale", "capitation_restant_nationale", nat_closed),
        [Paragraph("Capitation régionale (CAALA)", cat_label), Paragraph(_eur(reg_rate), cat_amount),
         Paragraph(_eur(cap_reg_due), cat_amount), Paragraph(_eur(cap_reg_enc), cat_amount)],
        _reconcile_row("↳ effectivement reversé / reste (CAALA)", "total_capitation_paid_regionale", "capitation_restant_regionale", reg_closed),
        [Paragraph("Total à reverser", ParagraphStyle("capt", parent=cat_label, fontName="Helvetica-Bold")),
         Paragraph(_eur(rate_total), ParagraphStyle("captr", parent=cat_amount)),
         Paragraph(_eur(cap_due_total), ParagraphStyle("captd", parent=cat_amount)),
         Paragraph(_eur(cap_enc_total), ParagraphStyle("capte", parent=cat_amount, textColor=AMBER_TXT))],
    ]
    if nat_closed or reg_closed:
        closed_note = []
        if nat_closed: closed_note.append("GODF")
        if reg_closed: closed_note.append("CAALA")
        cap_rows.append([
            Paragraph(f"ℹ️ {' et '.join(closed_note)} déclarée(s) soldée(s) par le trésorier — le calcul ci-dessus reste affiché pour référence.",
                      ParagraphStyle("capnote", parent=cat_label, fontSize=7.5, textColor=GRAY, fontName="Helvetica-Oblique")),
            "", "", "",
        ])
    cap_table = Table(cap_rows, colWidths=[6.4 * cm, 3.6 * cm, 3.6 * cm, 3.8 * cm])
    cap_table_style = [
        ("SPAN", (0, 0), (-1, 0)),
        ("BACKGROUND", (0, 0), (-1, 0), SLATE_BG),
        ("BACKGROUND", (0, 6), (-1, 6), AMBER_BG),
        ("LINEBELOW", (0, 1), (-1, 1), 0.75, colors.HexColor("#e5e7eb")),
        ("LINEBELOW", (0, 2), (-1, 5), 0.4, colors.HexColor("#f3f4f6")),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (0, -1), 8), ("RIGHTPADDING", (-1, 0), (-1, -1), 8),
    ]
    if len(cap_rows) > 7:
        cap_table_style.append(("SPAN", (0, 7), (-1, 7)))
    cap_table.setStyle(TableStyle(cap_table_style))
    story += [cap_table, Spacer(1, 0.15 * cm),
              Paragraph(
                  "La capitation (nationale + régionale) est due pour chaque membre en Appartenance, "
                  "collectée avec la cotisation puis intégralement reversée à l'obédience — elle transite "
                  "par la trésorerie de la loge sans en constituer une ressource, d'où sa déduction du "
                  "résultat net ci-dessus.",
                  note_style),
              Spacer(1, 0.45 * cm)]

    # ── Compte de résultat total (tous mouvements comptables de l'année) ────
    # Reprend l'intégralité des mouvements — fonctionnement de la loge ET
    # capitation réunis — correspond exactement aux totaux de la page
    # Mouvements (Recettes / Dépenses / Solde).
    solde_complet = float(data.get("solde_complet") or 0)
    solde_color = GREEN_TXT if solde_complet >= 0 else ORANGE_TXT
    total_cards = Table(
        [[
            card(GREEN_BG, colors.HexColor("#bbf7d0"), GREEN_TXT, "RECETTES",
                 _eur(data.get("total_recettes_complet") or 0), GREEN_TXT, "", width=5.6 * cm),
            card(RED_BG, colors.HexColor("#fecaca"), RED_TXT, "DÉPENSES",
                 _eur(data.get("total_depenses_complet") or 0), RED_TXT, "", width=5.6 * cm),
            card(GREEN_BG if solde_complet >= 0 else ORANGE_BG,
                 colors.HexColor("#bbf7d0") if solde_complet >= 0 else colors.HexColor("#fed7aa"),
                 solde_color, "SOLDE", fmt_signed(solde_complet), solde_color, "", width=5.6 * cm),
        ]],
        colWidths=[5.8 * cm, 5.8 * cm, 5.8 * cm],
    )
    total_cards.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]))
    story += [
        Paragraph("Compte de résultat total", ParagraphStyle(
            "crt", parent=styles["Normal"], fontSize=11, fontName="Helvetica-Bold", textColor=DARK, spaceAfter=3,
        )),
        Paragraph(
            "Reprend l'intégralité des mouvements comptables de l'année — fonctionnement de la loge ET capitation "
            "(collectée et reversée) réunis en un seul solde. Correspond exactement aux totaux de la page Mouvements.",
            note_style,
        ),
        Spacer(1, 0.15 * cm),
        total_cards,
        Spacer(1, 0.5 * cm),
    ]

    # ── Situation des cotisations ───────────────────────────────────────────
    creances = data["creances"]
    cot_rows = [
        [Paragraph("Cotisations appelées (total dû, année complète)", cat_label), Paragraph(_eur(data["total_cotisations_due"]), cat_amount)],
        [Paragraph("Cotisations brutes encaissées", cat_label), Paragraph(_eur(data["total_cotisations"]), ParagraphStyle("g", parent=cat_amount, textColor=GREEN_TXT))],
        [Paragraph("dont capitation à reverser (voir ci-dessus)", ParagraphStyle("sub", parent=cat_label, fontSize=8.5, textColor=GRAY)),
         Paragraph(f"− {_eur(cap_enc_total)}", ParagraphStyle("subamt", parent=cat_amount, fontSize=8.5, fontName="Helvetica", textColor=GRAY))],
        [Paragraph("Cotisations nettes encaissées (part loge)", ParagraphStyle("netl", parent=cat_label, fontName="Helvetica-Bold")),
         Paragraph(_eur(data["total_cotisations_nettes"]), ParagraphStyle("neta", parent=cat_amount, textColor=GREEN_TXT))],
        [Paragraph("Créances (cotisations appelées non encaissées)", ParagraphStyle("cl", parent=cat_label, fontName="Helvetica-Bold")),
         Paragraph(_eur(creances), ParagraphStyle("ca", parent=cat_amount, textColor=(AMBER_TXT if creances > 0 else GREEN_TXT)))],
    ]
    cot_header_style = ParagraphStyle("coth", parent=section_title, textColor=colors.white)
    cot_table = Table(
        [[Paragraph("Situation des cotisations", cot_header_style)]] + [[r[0], r[1]] for r in cot_rows],
        colWidths=[14.8 * cm, 2.6 * cm],
    )
    cot_table.setStyle(TableStyle([
        ("SPAN", (0, 0), (-1, 0)),
        ("BACKGROUND", (0, 0), (-1, 0), SLATE_BG),
        ("BACKGROUND", (0, 5), (-1, 5), AMBER_BG if creances > 0 else colors.HexColor("#f9fafb")),
        ("LINEBELOW", (0, 1), (-1, 4), 0.4, colors.HexColor("#f3f4f6")),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (0, -1), 8), ("RIGHTPADDING", (-1, 0), (-1, -1), 8),
    ]))
    story += [cot_table, Spacer(1, 0.5 * cm)]

    # ── Budget vs Réalisé ───────────────────────────────────────────────────
    # Même logique que la page Bilan : une catégorie "hors budget" (aucune
    # ligne votée, ex. une biennale autofinancée par ses propres recettes) ou
    # "clôturée" (déclarée réglée par le trésorier, cf. BudgetLine.closed)
    # n'entre dans aucun des deux totaux — sinon une dépense hors budget peut
    # gonfler artificiellement le "réalisé" et fausser l'écart total.
    if data["budget_by_cat"]:
        all_cats = sorted(set(list(data["budget_by_cat"].keys()) + list(data["charges_by_cat"].keys())))
        closed_by_cat = data.get("closed_by_cat") or {}
        has_estimate_by_cat = data.get("has_estimate_by_cat") or {}
        estimate_by_cat = data.get("estimate_by_cat") or {}
        th = ParagraphStyle("th", parent=styles["Normal"], fontSize=8.5, textColor=GRAY, fontName="Helvetica-Bold")
        td = ParagraphStyle("td", parent=styles["Normal"], fontSize=9)
        td_r = ParagraphStyle("tdr", parent=td, alignment=TA_RIGHT)

        rows = [[
            Paragraph("Catégorie", th), Paragraph("Prévu", ParagraphStyle("thr", parent=th, alignment=TA_RIGHT)),
            Paragraph("Réalisé", ParagraphStyle("thr2", parent=th, alignment=TA_RIGHT)),
            Paragraph("Écart", ParagraphStyle("thr3", parent=th, alignment=TA_RIGHT)),
            Paragraph("Compté dans le Bilan", ParagraphStyle("thr4", parent=th, alignment=TA_RIGHT)),
        ]]
        for cat_name in all_cats:
            prevu = data["budget_by_cat"].get(cat_name, Decimal("0"))
            realise = data["charges_by_cat"].get(cat_name, Decimal("0"))
            hors_budget = cat_name not in data["budget_by_cat"]
            a_estimation = bool(has_estimate_by_cat.get(cat_name)) and not hors_budget
            cloturee = bool(closed_by_cat.get(cat_name)) and not hors_budget and not a_estimation
            exclu = hors_budget or cloturee or a_estimation
            ecart = prevu - realise
            ecart_color = RED_TXT if ecart < 0 else (GREEN_TXT if ecart > 0 else GRAY)
            ecart_str = f"{'+' if ecart > 0 else ''}{_eur(ecart)}"
            suffix = " (hors budget)" if hors_budget else (" (clôturée)" if cloturee else (" (estimation trésorier)" if a_estimation else ""))
            if a_estimation:
                compte_str = _eur(estimate_by_cat.get(cat_name, Decimal("0")))
                compte_color = colors.HexColor("#5a3fa8")  # teinte dédiée estimation trésorier
            else:
                compte_str = "—" if exclu else ecart_str
                compte_color = GRAY
            rows.append([
                Paragraph(cat_name + suffix, ParagraphStyle("catn", parent=td, textColor=(GRAY if exclu else DARK))),
                Paragraph(_eur(prevu), ParagraphStyle("tdp", parent=td_r, textColor=(GRAY if exclu else DARK))),
                Paragraph(_eur(realise), ParagraphStyle("tdr2", parent=td_r, textColor=(GRAY if exclu else DARK))),
                Paragraph(ecart_str, ParagraphStyle("ec", parent=td_r, textColor=ecart_color)),
                Paragraph(compte_str, ParagraphStyle("ecc", parent=td_r, textColor=compte_color)),
            ])

        reste = data.get("reste_a_payer_budget", Decimal("0"))
        depassement = data.get("depassements_budget", Decimal("0"))
        total_prevu = data["total_budget"]
        total_realise = data["total_charges"]
        ecart_total_brut = total_prevu - total_realise
        ecart_total_color = RED_TXT if ecart_total_brut < 0 else (GREEN_TXT if ecart_total_brut > 0 else GRAY)
        ecart_lines = f"+{_eur(reste)} reste"
        if depassement > Decimal("0.5"):
            ecart_lines += f"<br/>−{_eur(depassement)} dépassé"
        rows.append([
            Paragraph("Total", ParagraphStyle("tl", parent=td, fontName="Helvetica-Bold")),
            Paragraph(_eur(total_prevu), ParagraphStyle("tp", parent=td_r, fontName="Helvetica-Bold")),
            Paragraph(_eur(total_realise), ParagraphStyle("tr2", parent=td_r, fontName="Helvetica-Bold")),
            Paragraph(f"{'+' if ecart_total_brut > 0 else ''}{_eur(ecart_total_brut)}",
                      ParagraphStyle("tecb", parent=td_r, fontName="Helvetica-Bold", textColor=ecart_total_color)),
            Paragraph(ecart_lines, ParagraphStyle(
                "tec", parent=td_r, fontName="Helvetica-Bold",
                textColor=(GREEN_TXT if depassement <= Decimal("0.5") else RED_TXT),
            )),
        ])

        budget_table = Table(rows, colWidths=[6.0 * cm, 2.9 * cm, 2.9 * cm, 2.9 * cm, 3.5 * cm])
        budget_table.setStyle(TableStyle([
            ("LINEBELOW", (0, 0), (-1, 0), 0.75, colors.HexColor("#e5e7eb")),
            ("LINEBELOW", (0, 1), (-1, -2), 0.4, colors.HexColor("#f3f4f6")),
            ("LINEABOVE", (0, -1), (-1, -1), 1, colors.HexColor("#9ca3af")),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(KeepTogether([
            Paragraph("Budget prévisionnel vs Réalisé", ParagraphStyle(
                "bh", parent=styles["Normal"], fontSize=11, fontName="Helvetica-Bold",
                textColor=DARK, spaceAfter=3,
            )),
            Paragraph(
                "La colonne « Écart » est le calcul mécanique prévu − réalisé, toujours affiché. La colonne "
                "« Compté dans le Bilan » est ce qui entre réellement dans les totaux ci-dessous : les lignes "
                "« hors budget » (aucun montant voté) et « clôturées » (déclarées réglées par le trésorier) n'y "
                "comptent pas, et une ligne avec une « estimation trésorier » y affiche ce montant à la place du "
                "calcul mécanique.", note_style,
            ),
            Spacer(1, 0.15 * cm),
            budget_table,
        ]))
        story.append(Spacer(1, 0.5 * cm))

    # ── Pied de page ─────────────────────────────────────────────────────────
    story.append(Paragraph(
        f"Document généré automatiquement le {datetime.now().strftime('%d/%m/%Y à %H:%M')} "
        f"par le Portail des membres — {lodge_name}.",
        ParagraphStyle("footer", parent=styles["Normal"], fontSize=7.5, textColor=colors.HexColor("#9ca3af")),
    ))

    doc.build(story)
    return buf.getvalue()
