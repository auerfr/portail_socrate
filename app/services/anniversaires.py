"""Service anniversaires maçonniques — calcul + email J-1."""
import asyncio
import logging
from datetime import date, timedelta
from typing import NamedTuple

logger = logging.getLogger(__name__)


class Anniversaire(NamedTuple):
    member_id: int
    first_name: str
    last_name: str
    email: str | None
    event_label: str   # "Initiation", "Passage", "Élévation"
    event_date: date   # date originale
    years: int         # nombre d'années
    anniv_date: date   # date de l'anniversaire cette année


def _anniv_this_year(event_date: date, ref_year: int) -> date | None:
    """Retourne la date d'anniversaire pour ref_year, None si bissextile impossible."""
    try:
        return event_date.replace(year=ref_year)
    except ValueError:
        # 29 févr sur année non-bissextile → 28 févr
        return event_date.replace(year=ref_year, day=28)


def compute_anniversaires(members, today: date | None = None) -> list[Anniversaire]:
    """Calcule tous les anniversaires maçonniques de l'année courante."""
    if today is None:
        today = date.today()
    year = today.year
    results = []

    for m in members:
        for field, label in [
            ("birth_date", "Naissance"),
            ("initiation_date", "Initiation"),
            ("companion_date", "Passage"),
            ("master_date", "Élévation"),
        ]:
            event_date = getattr(m, field, None)
            if not event_date:
                continue
            anniv = _anniv_this_year(event_date, year)
            if anniv is None:
                continue
            years = year - event_date.year
            if years <= 0:
                continue
            results.append(Anniversaire(
                member_id=m.id,
                first_name=m.first_name,
                last_name=m.last_name,
                email=getattr(m, "email", None),
                event_label=label,
                event_date=event_date,
                years=years,
                anniv_date=anniv,
            ))

    results.sort(key=lambda a: (a.anniv_date.month, a.anniv_date.day, a.last_name))
    return results


def upcoming(members, days: int = 30, today: date | None = None) -> list[Anniversaire]:
    """Anniversaires dans les N prochains jours (à partir d'aujourd'hui inclus)."""
    if today is None:
        today = date.today()
    end = today + timedelta(days=days)
    all_ann = compute_anniversaires(members, today)
    return [a for a in all_ann if today <= a.anniv_date <= end]


def tomorrow_anniversaires(members, today: date | None = None) -> list[Anniversaire]:
    """Anniversaires de demain."""
    if today is None:
        today = date.today()
    tomorrow = today + timedelta(days=1)
    all_ann = compute_anniversaires(members, today)
    return [a for a in all_ann if a.anniv_date == tomorrow]


async def send_anniversary_email(anniv: Anniversaire, lodge_name: str) -> bool:
    """Envoie un email de rappel au membre la veille de son anniversaire."""
    if not anniv.email:
        return False

    from app.config import get_settings
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    s = get_settings()
    if not s.smtp_host:
        return False

    is_birthday = (anniv.event_label == "Naissance")
    if is_birthday:
        subject = f"[{lodge_name}] Demain, vous fêtez vos {anniv.years} ans"
        intro = f"La loge <strong>{lodge_name}</strong> vous souhaite un très joyeux anniversaire !"
        big_line = f"🎂 {anniv.years} ans"
        outro = "Que cette nouvelle année vous apporte joie, santé et lumière."
    else:
        subject = f"[{lodge_name}] Demain, vous fêtez vos {anniv.years} ans de {anniv.event_label.lower()}"
        intro = f"La loge <strong>{lodge_name}</strong> vous souhaite un cordial V∴ F∴ à l'occasion de votre anniversaire maçonnique."
        big_line = f"🎖 {anniv.years} ans d'<strong>{anniv.event_label}</strong>"
        outro = "Que vos travaux continuent d'éclairer la loge. À bientôt sous le maillet !"

    body_html = f"""
<div style="font-family:Georgia,serif;max-width:600px;margin:0 auto;padding:2rem;color:#111;">
  <p style="color:#1a5252;font-weight:bold;font-size:1.1rem;">{'Cher' if is_birthday else 'V∴ F∴'} {anniv.first_name} {anniv.last_name},</p>
  <p>{intro}</p>
  <p style="font-size:1.3rem;text-align:center;margin:2rem 0;color:#1a5252;">
    {big_line}<br>
    <span style="font-size:0.9rem;color:#6b7280;">le {anniv.event_date.strftime('%d/%m/%Y')}</span>
  </p>
  <p>{outro}</p>
  <p style="font-size:0.8rem;color:#9ca3af;margin-top:2rem;">
    — Le Portail {lodge_name}
  </p>
</div>"""

    msg = MIMEMultipart("alternative")
    msg["From"] = s.smtp_from
    msg["To"] = anniv.email
    msg["Subject"] = subject
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    try:
        if s.smtp_secure == "ssl":
            srv = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=20)
        else:
            srv = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20)
            if s.smtp_secure == "tls":
                srv.starttls()
        if s.smtp_user:
            srv.login(s.smtp_user, s.smtp_pass)
        srv.sendmail(s.smtp_from, [anniv.email], msg.as_string())
        srv.quit()
        logger.info("Email anniversaire envoyé à %s %s <%s>", anniv.first_name, anniv.last_name, anniv.email)
        return True
    except Exception as exc:
        logger.error("Échec email anniversaire %s : %s", anniv.email, exc)
        return False


async def daily_anniversary_loop(get_active_members_fn, get_lodge_name_fn):
    """Boucle asyncio : chaque jour à ~7h, envoie les emails d'anniversaire J-1."""
    import datetime as _dt

    # Attendre jusqu'au prochain 7h00
    async def _sleep_until_7am():
        now = _dt.datetime.now()
        next_7 = now.replace(hour=7, minute=0, second=0, microsecond=0)
        if now >= next_7:
            next_7 += _dt.timedelta(days=1)
        wait = (next_7 - now).total_seconds()
        logger.info("Prochain envoi anniversaires dans %.0f s (%s)", wait, next_7.strftime("%d/%m %H:%M"))
        await asyncio.sleep(wait)

    await asyncio.sleep(30)  # petit délai au démarrage
    while True:
        await _sleep_until_7am()
        try:
            members = await get_active_members_fn()
            lodge_name = await get_lodge_name_fn()
            ann_demain = tomorrow_anniversaires(members)
            for a in ann_demain:
                await asyncio.get_event_loop().run_in_executor(
                    None, lambda a=a: asyncio.run(send_anniversary_email(a, lodge_name))
                )
            # Notifs push : J-1 rappel
            try:
                from app.database import AsyncSessionLocal
                from app.services.push import send_push_to_member
                async with AsyncSessionLocal() as s:
                    for a in ann_demain:
                        if a.event_label == "Naissance":
                            title = f"🎂 Demain : vos {a.years} ans"
                            body = f"La loge {lodge_name} vous souhaite un joyeux anniversaire !"
                        else:
                            title = f"🎖 Demain : {a.years} ans de {a.event_label.lower()}"
                            body = f"Votre anniversaire maçonnique avec {lodge_name}."
                        await send_push_to_member(s, a.member_id, title, body, "/anniversaires/")
            except Exception as exc:
                logger.warning("Push anniversaires : %s", exc)
            logger.info("Rappels anniversaires J-1 : %d envoyé(s)", len(ann_demain))
        except Exception as exc:
            logger.error("Erreur boucle anniversaires : %s", exc, exc_info=True)
