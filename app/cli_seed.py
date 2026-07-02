"""Seed demo mails — wstawia ~30 testowych maili w 3 skrzynkach.

3 skrzynki:
  - kontakt@asystencibiznesowi.pl  (account_id 1, real)
  - sklep@asystencibiznesowi.pl    (DEMO, fikcyjne konto, zamowienia/dostepy)
  - support@asystencibiznesowi.pl  (DEMO, fikcyjne konto, problemy techniczne)

Użycie:
    sudo -u maildash bash -c 'cd /opt/mail-dashboard && \\
        .venv/bin/python -m app.cli_seed'
"""
import json
from datetime import datetime, timedelta
from sqlalchemy import select

from .db import SessionLocal
from .models import Account, Mail


# ====================================================================
# DEMO ACCOUNTS — fikcyjne, dodawane jeśli nie istnieją
# ====================================================================
DEMO_ACCOUNTS = [
    {
        "email": "sklep@asystencibiznesowi.pl",
        "label": "Sklep (DEMO)",
        "connector_type": "imap",
        "imap_host": "demo.invalid",
        "imap_port": 993,
        "smtp_host": "demo.invalid",
        "smtp_port": 465,
    },
    {
        "email": "support@asystencibiznesowi.pl",
        "label": "Support (DEMO)",
        "connector_type": "imap",
        "imap_host": "demo.invalid",
        "imap_port": 993,
        "smtp_host": "demo.invalid",
        "smtp_port": 465,
    },
]


# ====================================================================
# MAILE — 3 typy:
#   "auto"     = lead_cena/lead_demo/klient_potwierdzenie (auto_reply candidate)
#   "manual"   = wymaga obróbki Adama (draft/decyzja)
#   "archive"  = newsletter/powiadomienie (auto-archive)
# ====================================================================
DEMO_MAILS = [
    # ============= SKRZYNKA #1: kontakt@ (główna, 12 maili) =============
    # AUTO-REPLY (3) - lead_cena, lead_demo, klient_potwierdzenie
    {"acc": 0, "from_email": "marek.kowalczyk@gmail.com", "from_name": "Marek Kowalczyk",
     "subject": "ile to kosztuje?",
     "body": "no hej\nwszedłem na waszą stronę przez reklamę na fb. ile za tych asystentów? "
             "i co właściwie dostaję bo nie do końca rozumiem czy to kurs czy gotowy system.",
     "minutes_ago": 45},
    {"acc": 0, "from_email": "anna.nowak@studioceramika.pl", "from_name": "Anna Nowak",
     "subject": "demo / call",
     "body": "Dzień dobry,\n\nprowadzę pracownię ceramiki online (~120 zamówień/mc). Zastanawiam się "
             "czy to coś dla mnie, ale ciężko mi ocenić z samej strony.\n\nMacie jakieś nagranie demo "
             "albo dałoby się umówić 15 minut żeby pokazać jak to wygląda w praktyce?\n\nAnna",
     "minutes_ago": 30},
    {"acc": 0, "from_email": "agnieszka.kowal@gmail.com", "from_name": "Agnieszka Kowal",
     "subject": "Re: tak",
     "body": "Spoko, działa już. Dzięki!",
     "minutes_ago": 8},

    # WYMAGA OBRÓBKI (3) - reklamacja, partnership, lead_pytanie
    {"acc": 0, "from_email": "krzysztof.zielinski@gmail.com", "from_name": "Krzysztof Zieliński",
     "subject": "nie działa",
     "body": "Kupiłem to wasze 2 dni temu. Próbowałem trzy razy odpalić tych Boss "
             "i Egzek w ChacieGPT i ZA KAŻDYM razem dostaję jakieś błędy że nie znajduje plików.\n\n"
             "Albo coś jest źle z waszymi instrukcjami albo to po prostu nie działa. "
             "97 zł poszło i siedzę 2 godziny próbując.\n\nChcę zwrot, nie mam czasu na takie zabawy.",
     "minutes_ago": 50},
    {"acc": 0, "from_email": "karolina.muller@freelancercheck.pl", "from_name": "Karolina Müller",
     "subject": "współpraca - newsletter dla freelancerów",
     "body": "Cześć Adam,\n\nśledzę was na LI od kilku miesięcy, fajna robota. Prowadzę newsletter "
             "FreelancerCheck (5k subskrybentów, głównie dev/design/marketing freelancerzy z PL). "
             "Otwieralność stabilnie 32%.\n\nMyślałam o wspólnym webinarze albo gostkowym wpisie u mnie. "
             "Co Ty na to?\n\nPozdrawiam\nKarolina",
     "minutes_ago": 240},
    {"acc": 0, "from_email": "tomek.lis@gmail.com", "from_name": "Tomek Lis",
     "subject": "Klon",
     "body": "Hej,\nczytałem o tym Klonie i szczerze mówiąc trochę nie wierzę że AI naprawdę "
             "może pisać moim stylem. Skąd niby ma wiedzieć jak ja piszę jeśli "
             "dopiero zaczynam? Czy mam mu wgrać moje stare maile czy jak to działa?\n\nT.",
     "minutes_ago": 70},

    # AUTO-ARCHIVE (6) - newsletter, powiadomienia
    {"acc": 0, "from_email": "noreply@mailerlite.com", "from_name": "MailerLite",
     "subject": "Twoja kampania została wysłana",
     "body": "Cześć,\n\nTwoja kampania 'Newsletter #42' została pomyślnie wysłana do 1247 odbiorców.\n\n"
             "Pierwsze statystyki będą dostępne za 1h.\n\nMailerLite Team",
     "minutes_ago": 12},
    {"acc": 0, "from_email": "no-reply@hotpay.pl", "from_name": "HotPay",
     "subject": "Potwierdzenie płatności #HP-2845712",
     "body": "Otrzymaliśmy płatność:\n\nKwota: 97.00 PLN\nKlient: Agnieszka Kowal\n"
             "Produkt: Asystenci Biznesowi\nStatus: ZAKSIĘGOWANE",
     "minutes_ago": 35},
    {"acc": 0, "from_email": "info@linkedin.com", "from_name": "LinkedIn",
     "subject": "Adam, masz 12 nowych powiadomień",
     "body": "5 nowych osób oglądało Twój profil w tym tygodniu.\n3 zaproszenia do połączenia.\n"
             "Zobacz aktywność: linkedin.com/feed",
     "minutes_ago": 95},
    {"acc": 0, "from_email": "noreply@allegro.pl", "from_name": "Allegro",
     "subject": "Twoja paczka jest w drodze",
     "body": "Status zamówienia A-558239841: WYSŁANE\nNumer: InPost 624000123456789012345\n"
             "Termin doręczenia: piątek",
     "minutes_ago": 180},
    {"acc": 0, "from_email": "no-reply@github.com", "from_name": "GitHub",
     "subject": "[mail-dashboard] Dependabot opened a PR",
     "body": "Bumps pdfplumber from 0.10.3 to 0.11.0.\n\nView PR: https://github.com/adam/mail-dashboard/pull/42",
     "minutes_ago": 220},
    {"acc": 0, "from_email": "newsletter@harvardbiznes.pl", "from_name": "Harvard Business Review",
     "subject": "Tygodniówka: 5 trendów AI w Q2 2026",
     "body": "Najlepsze materiały tego tygodnia:\n\n1. Jak GPT-5 zmienia produktywność w MŚP\n"
             "2. Polskie startupy AI które warto śledzić\n\nCzytaj: hbrp.pl/newsletter/2026-05-14",
     "minutes_ago": 540},

    # ============= SKRZYNKA #2: sklep@ (zamówienia/dostępy, 11 maili) =============
    # AUTO-REPLY (5) - lead_cena ×2, klient_potwierdzenie ×2, lead_demo
    {"acc": 1, "from_email": "marta.bartosz@gmail.com", "from_name": "Marta Bartosz",
     "subject": "cena Asystentów",
     "body": "Cześć, ile to kosztuje? Widziałam reklamę.",
     "minutes_ago": 22},
    {"acc": 1, "from_email": "kuba.malicki@gmail.com", "from_name": "Kuba Malicki",
     "subject": "Pytanie o cenę",
     "body": "Hej. Ile za pełną ofertę? Pozdrawiam, Kuba",
     "minutes_ago": 65},
    {"acc": 1, "from_email": "natalia.frankowska@gmail.com", "from_name": "Natalia Frankowska",
     "subject": "dziękuję!",
     "body": "Już wszystko działa, świetnie! Wielkie dzięki.",
     "minutes_ago": 18},
    {"acc": 1, "from_email": "jacek.morawski@gmail.com", "from_name": "Jacek Morawski",
     "subject": "Re: ok",
     "body": "Spoko, dzięki za szybką odpowiedź!",
     "minutes_ago": 4},
    {"acc": 1, "from_email": "monika.gawel@studio42.pl", "from_name": "Monika Gaweł",
     "subject": "Krótkie demo?",
     "body": "Hej, można rzucić okiem na demo? Jestem founderką małego studia (3 osoby), "
             "zastanawiam się czy się uda zaadaptować to u nas.\n\nMonika",
     "minutes_ago": 55},

    # WYMAGA OBRÓBKI (3)
    {"acc": 1, "from_email": "magda.wisniewska@gmail.com", "from_name": "Magda Wiśniewska",
     "subject": "Re: dostęp do panelu",
     "body": "Hej, kupiłam wczoraj. Już wszedłam do panelu, fajnie wygląda.\n\nAle nie wiem za bardzo "
             "od czego zacząć. Co zrobić w pierwszej kolejności żeby Klon zaczął się uczyć "
             "mojego stylu? Mam mu coś wkleić?",
     "minutes_ago": 90},
    {"acc": 1, "from_email": "marek.kowalczyk@gmail.com", "from_name": "Marek Kowalczyk",
     "subject": "Re: jeszcze",
     "body": "aa i jeszcze jedno, da się wziąć tylko Klona osobno? "
             "bo w sumie głównie chodzi mi o pisanie maili a reszta narazie mnie nie interesuje",
     "minutes_ago": 5},
    {"acc": 1, "from_email": "lukasz.dabrowski@gmail.com", "from_name": "Łukasz Dąbrowski",
     "subject": "płatność",
     "body": "Halo, próbuję drugi raz dziś zapłacić za asystentów i przy karcie "
             "mi wywala 'transakcja odrzucona'. Próbowałem dwiema kartami, kasa jest, "
             "limity ok, dzwoniłem do banku, mówią że po stronie sprzedawcy.\n\n"
             "Mogę przelewem? Chętnie wezmę dziś bo jutro lecę za granicę.",
     "minutes_ago": 40},

    # AUTO-ARCHIVE (3)
    {"acc": 1, "from_email": "no-reply@hotpay.pl", "from_name": "HotPay",
     "subject": "Potwierdzenie płatności #HP-2845891",
     "body": "Otrzymaliśmy płatność: 97.00 PLN\nKlient: Marta Bartosz\nProdukt: Asystenci Biznesowi",
     "minutes_ago": 25},
    {"acc": 1, "from_email": "no-reply@hotpay.pl", "from_name": "HotPay",
     "subject": "Potwierdzenie płatności #HP-2845902",
     "body": "Otrzymaliśmy płatność: 67.00 PLN\nKlient: Filip Mazur\nProdukt: MARK Mini",
     "minutes_ago": 110},
    {"acc": 1, "from_email": "info@allegro.pl", "from_name": "Allegro",
     "subject": "Nowy komentarz pod ofertą",
     "body": "Dostałeś komentarz do oferty A-994851: 'Polecam, szybka wysyłka'.",
     "minutes_ago": 320},

    # ============= SKRZYNKA #3: support@ (problemy techniczne, 10 maili) =============
    # AUTO-REPLY (3) - klient_potwierdzenie ×2, lead_cena (rzadziej tu)
    {"acc": 2, "from_email": "ewa.kos@kreatywnabaza.pl", "from_name": "Ewa Kos",
     "subject": "Re: dzięki",
     "body": "ok zadziałało, dzięki za pomoc!",
     "minutes_ago": 14},
    {"acc": 2, "from_email": "patryk.lewandowski@gmail.com", "from_name": "Patryk Lewandowski",
     "subject": "działa",
     "body": "OK, teraz działa, dzięki",
     "minutes_ago": 32},
    {"acc": 2, "from_email": "bartek.szumski@gmail.com", "from_name": "Bartek Szumski",
     "subject": "jednorazowo czy abonament?",
     "body": "Hej, zastanawiam się nad zakupem. Płacę raz i mam dostęp na zawsze, czy jakaś subskrypcja miesięczna?",
     "minutes_ago": 48},

    # WYMAGA OBRÓBKI (5)
    {"acc": 2, "from_email": "pawel.szymanski@gmail.com", "from_name": "Paweł Szymański",
     "subject": "panel - error",
     "body": "Cześć,\nod rana nie mogę się zalogować do panelu. Wpisuję maila na który "
             "kupiłem (ten) i dostaję komunikat 'Błąd 500 - serwer'.\n\n"
             "Próbowałem na chrome i firefox, to samo. Czyściłem cache.",
     "minutes_ago": 25},
    {"acc": 2, "from_email": "iza.kalinska@brandfactory.pl", "from_name": "Iza Kalińska",
     "subject": "Discord nie działa",
     "body": "Hej, dostałam zaproszenie do Discorda po zakupie ale link wygasł czy coś, "
             "bo wskakuje że 'invalid invite'. Nowy link?",
     "minutes_ago": 75},
    {"acc": 2, "from_email": "rafal.mikolajewski@gmail.com", "from_name": "Rafał Mikołajewski",
     "subject": "KLON nie zachowuje stylu",
     "body": "Mam już Asystentów od tygodnia. KLON pisze jak chatGPT, sucho i sztywno. "
             "Wkleiłem mu 5 swoich starych maili, dalej tak samo. Co robię źle?",
     "minutes_ago": 130},
    {"acc": 2, "from_email": "ola.szczepanik@gmail.com", "from_name": "Ola Szczepanik",
     "subject": "FAQ na stronie?",
     "body": "Witam, zanim kupię chcę zapytać. Czy macie gdzieś FAQ albo bazę pytań? "
             "Brakuje mi info typu: czy działa też z polskim kontekstem prawniczym, "
             "czy są bonusy itp.",
     "minutes_ago": 200},
    {"acc": 2, "from_email": "wojtek.szymczyk@b2bsoft.pl", "from_name": "Wojtek Szymczyk",
     "subject": "Custom dla mojej firmy",
     "body": "Cześć Adam,\n\nprowadzę software house (8 osób), pracujemy głównie z B2B. "
             "Wasi Asystenci wyglądają fajnie ale mam zerową ochotę na konfigurację.\n\n"
             "Robicie wdrożenia szyte pod konkretną firmę? Coś w stylu done-for-you?",
     "minutes_ago": 290},

    # AUTO-ARCHIVE (2)
    {"acc": 2, "from_email": "noreply@uptimerobot.com", "from_name": "UptimeRobot",
     "subject": "Monitor is UP: dashboard.asystencibiznesowi.pl",
     "body": "Monitor 'Mail Dashboard' jest UP od 14:32:11.\nPoprzedni downtime: 0 min",
     "minutes_ago": 60},
    {"acc": 2, "from_email": "newsletter@indiehackers.com", "from_name": "Indie Hackers",
     "subject": "Top posts of the week",
     "body": "This week's most popular discussions:\n\n1. How I made $5k MRR with my AI tool\n"
             "2. The marketing playbook for solopreneurs",
     "minutes_ago": 720},

    # ============= EXTRA MAILE (rozłożone po skrzynkach, dużo auto-reply) =============

    # AUTO-REPLY (8 nowych — pokażą że Klon naprawdę dużo robi sam)
    {"acc": 0, "from_email": "filip.adamczyk@gmail.com", "from_name": "Filip Adamczyk",
     "subject": "Pytanie",
     "body": "Cześć, ile kosztuje Wasz produkt?", "minutes_ago": 17},
    {"acc": 0, "from_email": "kasia.bednarek@gmail.com", "from_name": "Kasia Bednarek",
     "subject": "cennik",
     "body": "Hej, prosiłabym o cennik. Pozdr Kasia", "minutes_ago": 38},
    {"acc": 0, "from_email": "robert.glowacki@gmail.com", "from_name": "Robert Głowacki",
     "subject": "Re: Super",
     "body": "Idealnie, dzięki!", "minutes_ago": 11},
    {"acc": 1, "from_email": "ola.kawa@gmail.com", "from_name": "Ola Kawa",
     "subject": "ile?",
     "body": "Cześć, ile za pełny dostęp?", "minutes_ago": 27},
    {"acc": 1, "from_email": "dominik.golec@gmail.com", "from_name": "Dominik Golec",
     "subject": "demo",
     "body": "Hej, da się rzucić okiem na demo? Prowadzę sklep online z butami.", "minutes_ago": 52},
    {"acc": 1, "from_email": "iza.witek@gmail.com", "from_name": "Iza Witek",
     "subject": "Re: super",
     "body": "Spoko dzięki!", "minutes_ago": 6},
    {"acc": 2, "from_email": "tomasz.kuc@gmail.com", "from_name": "Tomasz Kuc",
     "subject": "Re: ok",
     "body": "Działa, dziękuję :)", "minutes_ago": 19},
    {"acc": 2, "from_email": "marlena.kruk@gmail.com", "from_name": "Marlena Kruk",
     "subject": "ile za to",
     "body": "Hej, jaka cena za asystentów biznesowych?", "minutes_ago": 41},

    # MANUAL (4 nowe)
    {"acc": 0, "from_email": "agata.suchecka@vetclinic.pl", "from_name": "Agata Suchecka",
     "subject": "Klinika weterynaryjna - Wasz system?",
     "body": "Dzień dobry,\n\nprowadzimy klinikę weterynaryjną (3 lekarzy, ok. 200 wizyt/mc). "
             "Czy Wasi Asystenci dadzą radę z naszym kontekstem? Mamy specyficzne pytania klientów "
             "o szczepienia, leki, terminy.\n\nAgata", "minutes_ago": 110},
    {"acc": 1, "from_email": "michal.tracz@gmail.com", "from_name": "Michał Tracz",
     "subject": "Faktura VAT?",
     "body": "Hej, kupiłem 3 dni temu i dostałem tylko paragon. Potrzebuję fakturę VAT na firmę.\n"
             "NIP: 5252837451\nDanych firmowych dosyłam jak potwierdzicie że da się.", "minutes_ago": 80},
    {"acc": 2, "from_email": "joanna.lis@academy.pl", "from_name": "Joanna Lis",
     "subject": "Integracja z naszym CRM?",
     "body": "Cześć Adam,\n\nprowadzimy mały kurs online (ok. 800 uczestników). Asystenci wyglądają "
             "ciekawie ale potrzebowalibyśmy integracji z naszym CRM (HubSpot). Da się?\n\n"
             "Joanna, Academy42", "minutes_ago": 165},
    {"acc": 2, "from_email": "kamil.pieczonka@gmail.com", "from_name": "Kamil Pieczonka",
     "subject": "Po wczorajszym webinarze",
     "body": "Cześć! Byłem wczoraj na waszym webinarze o AI dla małych firm, świetna prezentacja. "
             "Mam pytanie - czy KLON poradzi sobie z mailami w języku angielskim? "
             "Połowa moich klientów to obcokrajowcy.", "minutes_ago": 95},

    # AUTO-ARCHIVE (2 nowe — newsletter/powiadomienie)
    {"acc": 0, "from_email": "newsletter@dailyai.com", "from_name": "Daily AI",
     "subject": "5 AI tools that exploded this week",
     "body": "This week's hottest AI tools:\n\n1. Cursor 2.0 release\n2. Anthropic's new Claude\n"
             "3. Open source alternatives", "minutes_ago": 480},
    {"acc": 1, "from_email": "no-reply@hotpay.pl", "from_name": "HotPay",
     "subject": "Potwierdzenie płatności #HP-2845999",
     "body": "Otrzymaliśmy płatność: 97.00 PLN\nKlient: Filip Adamczyk\nProdukt: Asystenci Biznesowi",
     "minutes_ago": 9},
]


def ensure_demo_accounts(db):
    """Tworzy fikcyjne konta sklep@ i support@ jeśli nie istnieją. Zwraca listę account_id."""
    ids = []
    # Pierwsze konto: pierwsza aktywna skrzynka (real)
    first = db.scalar(select(Account).where(Account.active.is_(True)).order_by(Account.id).limit(1))
    if not first:
        raise RuntimeError("Brak aktywnego konta — najpierw dodaj rzeczywiste przez add-imap/add-gmail")
    ids.append(first.id)

    # Demo accounts (jeśli nie istnieją)
    for spec in DEMO_ACCOUNTS:
        existing = db.scalar(select(Account).where(Account.email == spec["email"]))
        if existing:
            existing.active = True   # upewnij się że aktywne
            ids.append(existing.id)
            print(f"  · konto istniało: {spec['email']} (id={existing.id})")
        else:
            acc = Account(
                email=spec["email"],
                label=spec["label"],
                connector_type=spec["connector_type"],
                imap_host=spec["imap_host"],
                imap_port=spec["imap_port"],
                smtp_host=spec["smtp_host"],
                smtp_port=spec["smtp_port"],
                imap_password_encrypted=None,  # demo, brak realnych credentials
                active=True,
                last_fetch_at=datetime.utcnow(),
            )
            db.add(acc)
            db.flush()
            ids.append(acc.id)
            print(f"  + nowe konto demo: {spec['email']} (id={acc.id})")
    db.commit()
    return ids


def seed():
    now = datetime.utcnow()
    with SessionLocal() as db:
        account_ids = ensure_demo_accounts(db)
        print(f"\nSkrzynki w użyciu: {account_ids}\n")

        added = 0
        for i, m in enumerate(DEMO_MAILS, 1):
            acc_idx = m["acc"]
            if acc_idx >= len(account_ids):
                print(f"  ⚠ pomijam (brak konta idx={acc_idx}): {m['subject']}")
                continue
            acc_id = account_ids[acc_idx]
            received_at = now - timedelta(minutes=m["minutes_ago"])
            mail = Mail(
                account_id=acc_id,
                external_id=f"DEMO-{int(now.timestamp())}-{i}",
                thread_id=f"DEMO-THREAD-{i}",
                from_email=m["from_email"],
                from_name=m["from_name"],
                to_emails=json.dumps(["demo@asystencibiznesowi.pl"]),
                cc_emails=json.dumps([]),
                subject=m["subject"],
                body_text=m["body"],
                body_html=None,
                received_at=received_at,
                is_reply=m["subject"].lower().startswith("re:"),
                has_attachments=False,
                in_reply_to=None,
                status="new",
            )
            db.add(mail)
            added += 1
            acc_label = ["kontakt@", "sklep@", "support@"][acc_idx]
            print(f"  [{acc_label:>10}  +{m['minutes_ago']:>4}m]  {m['from_name']:25}  {m['subject']}")

        db.commit()
        print(f"\n✅ Wstawiono {added} demo maili w {len(set(m['acc'] for m in DEMO_MAILS))} skrzynkach.")
        print("   Analyzer obrobi je w ciągu 1 min, drafter w ciągu 2 min.")


if __name__ == "__main__":
    seed()
