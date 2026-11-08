"""Export PDF du budget prévisionnel, avec comparatif à l'année précédente
(voté et réalisé) — pour présentation en assemblée (ex. vote du budget N+1
fin d'année, face au bilan de l'année en cours)."""
import io
from datetime import datetime
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

TEAL = colors.HexColor("#2c7a7b")
GRAY = colors.HexColor("#6b7280")
DARK = colors.HexColor("#1f2937")
GREEN_TXT = colors.HexColor("#15803d")
RED_TXT = colors.HexColor("#b91c1c")
SLATE_BG = colors.HexColor("#4b5563")


def _eur(amount) -> str:
    sign = "-" if float(amount) < 0 else ""
    return f"{sign}{abs(float(amount)):,.2f} €".replace(",", " ").replace(".", ",")


def build_budget_pdf(lodge_name: str, comparison: dict) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        leftMargin=1.8 * cm, rightMargin=1.8 * cm,
        topMargin=1.8 * cm, bottomMargin=1.8 * cm,
    )
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("H1", parent=styles["Heading1"], textColor=TEAL, fontSize=18, spaceAfter=2)
    subtitle = ParagraphStyle("Subtitle", parent=styles["Normal"], fontSize=10.5, textColor=GRAY, spaceAfter=6)
    note_style = ParagraphStyle("Note", parent=styles["Normal"], fontSize=8.5, textColor=GRAY, leading=12, spaceAfter=10)

    year = comparison["year"]
    compare_year = comparison["compare_year"]
    compare_label = compare_year.label if compare_year else "—"
    year_is_proposal = bool(comparison.get("year_is_proposal"))
    year_label_col = "Proposé" if year_is_proposal else "Voté"

    story = [
        Paragraph("Budget proposé" if year_is_proposal else "Budget prévisionnel", h1),
        Paragraph(f"{lodge_name} — {year.label}" + (f" · comparé à {compare_label}" if compare_year else ""), subtitle),
    ]
    if year_is_proposal:
        story.append(Paragraph(
            f"Proposition soumise au vote de l'assemblée — le budget {year.label} n'est pas encore adopté.",
            ParagraphStyle("warn", parent=note_style, textColor=colors.HexColor("#b45309"), fontName="Helvetica-Bold"),
        ))
    story.append(Paragraph(
        f"Comparatif entre le budget {year_label_col.lower()} pour {year.label}, le budget voté pour "
        f"{compare_label} et le réalisé {compare_label} à ce jour — pour présentation en assemblée.",
        note_style,
    ))

    def section(title, rows, totals, show_projection):
        th = ParagraphStyle("th", parent=styles["Normal"], fontSize=8.5, textColor=GRAY, fontName="Helvetica-Bold")
        th_r = ParagraphStyle("thr", parent=th, alignment=TA_RIGHT)
        td = ParagraphStyle("td", parent=styles["Normal"], fontSize=9)
        td_r = ParagraphStyle("tdr", parent=td, alignment=TA_RIGHT)
        ecart_ref_label = "Écart / projeté" if show_projection else "Écart / réalisé"

        header_row = [Paragraph("Catégorie", th), Paragraph(f"Voté {compare_label}", th_r),
                      Paragraph(f"Réalisé {compare_label}\n(à ce jour)", th_r)]
        if show_projection:
            header_row.append(Paragraph(f"Projeté {compare_label}\n(fin d'année)", th_r))
        header_row += [Paragraph(f"{year_label_col} {year.label}", th_r),
                       Paragraph("Écart / voté", th_r), Paragraph(ecart_ref_label, th_r)]
        n_cols = len(header_row)
        table_rows = [header_row]
        for r in rows:
            ev = r["ecart_vs_vote"]
            er = r["ecart_vs_projete"] if show_projection else r["ecart_vs_realise"]
            row_cells = [
                Paragraph(r["cat"], td),
                Paragraph(_eur(r["vote_compare"]), td_r),
                Paragraph(_eur(r["realise_compare"]), td_r),
            ]
            if show_projection:
                row_cells.append(Paragraph(_eur(r["projete_compare"]), td_r))
            row_cells += [
                Paragraph(_eur(r["vote_year"]), td_r),
                Paragraph(f"{'+' if ev > 0 else ''}{_eur(ev)}", ParagraphStyle("ev", parent=td_r, textColor=(GREEN_TXT if ev > 0 else (RED_TXT if ev < 0 else GRAY)))),
                Paragraph(f"{'+' if er > 0 else ''}{_eur(er)}", ParagraphStyle("er", parent=td_r, textColor=(GREEN_TXT if er > 0 else (RED_TXT if er < 0 else GRAY)))),
            ]
            table_rows.append(row_cells)
        tv, tr, ty = totals["vote_compare"], totals["realise_compare"], totals["vote_year"]
        tp = totals.get("projete_compare", tr)
        tev = ty - tv
        ter = ty - (tp if show_projection else tr)
        total_cells = [
            Paragraph("Total", ParagraphStyle("tl", parent=td, fontName="Helvetica-Bold")),
            Paragraph(_eur(tv), ParagraphStyle("tp0", parent=td_r, fontName="Helvetica-Bold")),
            Paragraph(_eur(tr), ParagraphStyle("tp2", parent=td_r, fontName="Helvetica-Bold")),
        ]
        if show_projection:
            total_cells.append(Paragraph(_eur(tp), ParagraphStyle("tp2b", parent=td_r, fontName="Helvetica-Bold")))
        total_cells += [
            Paragraph(_eur(ty), ParagraphStyle("tp3", parent=td_r, fontName="Helvetica-Bold")),
            Paragraph(f"{'+' if tev > 0 else ''}{_eur(tev)}", ParagraphStyle("tev", parent=td_r, fontName="Helvetica-Bold", textColor=(GREEN_TXT if tev > 0 else (RED_TXT if tev < 0 else GRAY)))),
            Paragraph(f"{'+' if ter > 0 else ''}{_eur(ter)}", ParagraphStyle("ter", parent=td_r, fontName="Helvetica-Bold", textColor=(GREEN_TXT if ter > 0 else (RED_TXT if ter < 0 else GRAY)))),
        ]
        table_rows.append(total_cells)

        if not rows:
            table_rows = [header_row, [Paragraph("Aucune ligne", ParagraphStyle("e", parent=td, textColor=GRAY))] + [""] * (n_cols - 1)]

        col_width = 24.2 * cm / n_cols
        t = Table(table_rows, colWidths=[col_width] * n_cols)
        t.setStyle(TableStyle([
            ("LINEBELOW", (0, 0), (-1, 0), 0.75, colors.HexColor("#e5e7eb")),
            ("LINEBELOW", (0, 1), (-1, -2), 0.4, colors.HexColor("#f3f4f6")),
            ("LINEABOVE", (0, -1), (-1, -1), 1, colors.HexColor("#9ca3af")),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        header_table = Table(
            [[Paragraph(title, ParagraphStyle("sh", parent=styles["Normal"], fontSize=11,
                                               fontName="Helvetica-Bold", textColor=colors.white))]],
            colWidths=[24.2 * cm],
        )
        header_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), SLATE_BG),
            ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ]))
        elements = [header_table, Spacer(1, 0.15 * cm), t]
        if show_projection:
            elements.append(Spacer(1, 0.1 * cm))
            elements.append(Paragraph(
                f"« Projeté {compare_label} » = réalisé à ce jour + reste à payer estimé d'ici fin d'année "
                f"(estimation du trésorier quand elle existe, sinon calcul prévu − réalisé).",
                ParagraphStyle("projnote", parent=note_style, fontSize=7.5, spaceAfter=0),
            ))
        return KeepTogether(elements)

    story.append(section("CHARGES", comparison["charges_rows"], comparison["charges_totals"], True))
    story.append(Spacer(1, 0.6 * cm))
    story.append(section("RECETTES PRÉVISIONNELLES", comparison["income_rows"], comparison["income_totals"], False))
    story.append(Spacer(1, 0.6 * cm))

    story.append(Paragraph(
        f"Document généré automatiquement le {datetime.now().strftime('%d/%m/%Y à %H:%M')} "
        f"par le Portail des membres — {lodge_name}.",
        ParagraphStyle("footer", parent=styles["Normal"], fontSize=7.5, textColor=colors.HexColor("#9ca3af")),
    ))

    doc.build(story)
    return buf.getvalue()
