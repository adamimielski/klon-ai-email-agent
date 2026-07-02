"""CLI do administracji mail-dashboard.

Komendy:
  init-db                                 - utwórz tabele
  gen-fernet-key                          - wygeneruj klucz Fernet
  add-imap <email> <host> <port> <smtp_host> <smtp_port> [--password-file FILE]
  add-gmail <email> --credentials FILE    - OAuth flow + zapis konta
  test-account <email>                    - test połączenia
  list-accounts                           - wypisz konta w DB
  fetch-now <email>                       - jednorazowy fetch (debug)
"""
import argparse
import getpass
import json
import sys
from pathlib import Path

from sqlalchemy import select

from .config import settings
from .db import SessionLocal, init_db
from .models import Account
from .security import encrypt, generate_key
from .connectors import connector_for


def _cmd_init_db():
    init_db()
    print(f"✅ DB zainicjalizowane: {settings.db_path}")


def _cmd_gen_fernet():
    key = generate_key()
    print(f"FERNET_KEY={key}")
    print("(dodaj do /opt/mail-dashboard/.env)")


def _cmd_add_imap(args):
    pwd = None
    if args.password_file:
        text = Path(args.password_file).read_text(encoding="utf-8")
        # plik w formacie KEY=VALUE
        for line in text.splitlines():
            if line.startswith("PASSWORD="):
                pwd = line.split("=", 1)[1].strip()
                break
    if not pwd:
        pwd = getpass.getpass("Hasło IMAP: ")
    if not pwd:
        print("❌ Brak hasła")
        sys.exit(1)

    with SessionLocal() as db:
        existing = db.scalar(select(Account).where(Account.email == args.email))
        if existing:
            print(f"⚠️  Konto {args.email} już istnieje (id={existing.id}). Aktualizuję hasło.")
            existing.imap_password_encrypted = encrypt(pwd)
            existing.imap_host = args.host
            existing.imap_port = args.port
            existing.smtp_host = args.smtp_host
            existing.smtp_port = args.smtp_port
            existing.active = True
        else:
            acc = Account(
                email=args.email,
                label=args.label or "IMAP",
                connector_type="imap",
                imap_host=args.host,
                imap_port=args.port,
                smtp_host=args.smtp_host,
                smtp_port=args.smtp_port,
                imap_password_encrypted=encrypt(pwd),
                active=True,
            )
            db.add(acc)
        db.commit()
    print(f"✅ Konto IMAP zapisane: {args.email}")


def _cmd_add_gmail(args):
    from google_auth_oauthlib.flow import InstalledAppFlow
    from .connectors.gmail import SCOPES

    creds_path = Path(args.credentials)
    if not creds_path.exists():
        print(f"❌ Plik {creds_path} nie istnieje")
        sys.exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
    creds = flow.run_local_server(
        port=args.port if args.port else 0,
        access_type="offline",
        prompt="consent",
        open_browser=False,
    )
    token_json = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes) if creds.scopes else SCOPES,
    }

    with SessionLocal() as db:
        existing = db.scalar(select(Account).where(Account.email == args.email))
        if existing:
            print(f"⚠️  Konto {args.email} istnieje (id={existing.id}). Aktualizuję token.")
            existing.refresh_token_encrypted = encrypt(json.dumps(token_json))
            existing.connector_type = "gmail_api"
            existing.active = True
        else:
            acc = Account(
                email=args.email,
                label=args.label or "Gmail",
                connector_type="gmail_api",
                refresh_token_encrypted=encrypt(json.dumps(token_json)),
                active=True,
            )
            db.add(acc)
        db.commit()
    print(f"✅ Konto Gmail zapisane: {args.email}")


def _cmd_test_account(args):
    with SessionLocal() as db:
        acc = db.scalar(select(Account).where(Account.email == args.email))
        if not acc:
            print(f"❌ Brak konta {args.email}")
            sys.exit(1)
        conn = connector_for(acc)
        ok, msg = conn.test_connection()
        if ok:
            print(f"✅ {acc.email}: {msg}")
        else:
            print(f"❌ {acc.email}: {msg}")
            sys.exit(2)


def _cmd_list_accounts():
    with SessionLocal() as db:
        accs = db.scalars(select(Account)).all()
        if not accs:
            print("(brak kont)")
            return
        for a in accs:
            print(f"  [{a.id:>3}] {a.email:<40} {a.connector_type:<10} "
                  f"active={a.active} label={a.label or '-'}")


def _cmd_fetch_now(args):
    from .workers.fetcher import fetch_account_once
    with SessionLocal() as db:
        acc = db.scalar(select(Account).where(Account.email == args.email))
        if not acc:
            print(f"❌ Brak konta {args.email}")
            sys.exit(1)
        count, err = fetch_account_once(db, acc)
        if err:
            print(f"⚠️  Błąd: {err}")
        print(f"✅ Pobrano {count} nowych maili")


def _cmd_count_sent(args):
    """Zlicza ile wysłanych jest w skrzynce (bez pobierania, szybkie)."""
    from .connectors import connector_for
    with SessionLocal() as db:
        acc = db.scalar(select(Account).where(Account.email == args.email))
        if not acc:
            print(f"❌ Brak konta {args.email}")
            sys.exit(1)
        conn = connector_for(acc)

        if acc.connector_type == "gmail_api":
            svc = conn._svc()
            label = svc.users().labels().get(userId="me", id="SENT").execute()
            n = label.get("messagesTotal", 0)
            print(f"📬 {acc.email}: {n} wiadomości w folderze SENT")
        else:
            # IMAP
            from imapclient import IMAPClient
            with IMAPClient(acc.imap_host, port=acc.imap_port, ssl=True) as c:
                from .security import decrypt
                c.login(acc.email, decrypt(acc.imap_password_encrypted))
                folders = [f[2] for f in c.list_folders()]
                sent_folder = None
                for cand in ["Sent", "Sent Items", "INBOX.Sent", "Wysłane",
                             "Wyslane", "Elementy wysłane"]:
                    if cand in folders:
                        sent_folder = cand
                        break
                if not sent_folder:
                    for f in folders:
                        if "sent" in f.lower() or "wysłan" in f.lower() or "wyslan" in f.lower():
                            sent_folder = f
                            break
                if not sent_folder:
                    print(f"❌ Nie znaleziono folderu Sent w {acc.email}")
                    print(f"   Dostępne foldery: {folders}")
                    return
                c.select_folder(sent_folder, readonly=True)
                n = len(c.search(["ALL"]))
                print(f"📬 {acc.email}: {n} wiadomości w folderze '{sent_folder}'")


def _cmd_import_sent(args):
    """Pobiera ostatnie N wysłanych + generuje embeddingi."""
    import json as json_lib
    from datetime import datetime
    from .connectors import connector_for
    from .models import SentExample
    from .embeddings import embed_texts, vec_to_bytes, EMBED_MODEL, cost_for_tokens
    from .models import ApiCost

    with SessionLocal() as db:
        acc = db.scalar(select(Account).where(Account.email == args.email))
        if not acc:
            print(f"❌ Brak konta {args.email}")
            sys.exit(1)

        # usuń stare (idempotentne)
        if args.reset:
            from sqlalchemy import delete
            db.execute(delete(SentExample).where(SentExample.account_id == acc.id))
            db.commit()
            print(f"🗑️  Usunięto stare przykłady dla {acc.email}")

        conn = connector_for(acc)
        print(f"📥 Pobieram do {args.limit} wysłanych z {acc.email}...")
        fetched = []
        for s in conn.list_sent(limit=args.limit):
            fetched.append(s)
            if len(fetched) % 20 == 0:
                print(f"   ...pobrano {len(fetched)}")
        print(f"✅ Pobrano {len(fetched)} wysłanych (po filtracji)")

        if not fetched:
            print("(nic do zapisania)")
            return

        # Zapis surowy
        records = []
        for s in fetched:
            rec = SentExample(
                account_id=acc.id,
                external_id=s.external_id,
                to_email=s.to_email,
                subject=s.subject,
                body_text=s.body_text,
                sent_at=s.sent_at,
                is_reply=s.is_reply,
                in_reply_to_body=s.in_reply_to_body,
            )
            db.add(rec)
            records.append(rec)
        db.flush()

        if args.no_embeddings:
            db.commit()
            print(f"✅ Zapisano {len(records)} przykładów (bez embeddingów)")
            return

        # Embeddingi w batchach po 50
        print(f"🧬 Generuję embeddingi (model: {EMBED_MODEL})...")
        BATCH = 50
        total_chars = 0
        for i in range(0, len(records), BATCH):
            batch = records[i:i + BATCH]
            texts = [r.body_text or "" for r in batch]
            total_chars += sum(len(t) for t in texts)
            vecs = embed_texts(texts)
            for rec, v in zip(batch, vecs):
                rec.embedding = vec_to_bytes(v)
                rec.embedding_model = EMBED_MODEL
            db.flush()
            print(f"   ...embed {i + len(batch)}/{len(records)}")

        # szacunkowy koszt (1 token ≈ 4 znaki)
        est_tokens = total_chars // 4
        est_cost = cost_for_tokens(est_tokens)
        cost_entry = ApiCost(
            timestamp=datetime.utcnow(),
            operation="embed",
            model=EMBED_MODEL,
            tokens_input=est_tokens,
            tokens_output=0,
            cost_usd=est_cost,
        )
        db.add(cost_entry)
        db.commit()
        print(f"✅ Embeddingi zapisane")
        print(f"   Szacunkowy koszt: ${est_cost:.4f} (~{est_tokens} tokenów)")


def _cmd_find_similar(args):
    """Debug: dla danego tekstu znajdź N najbardziej podobnych wysłanych."""
    from .models import SentExample
    from .embeddings import embed_one, bytes_to_vec, find_top_k

    print(f"🔍 Embedduję zapytanie...")
    query_vec = embed_one(args.query)

    with SessionLocal() as db:
        rows = db.scalars(
            select(SentExample).where(SentExample.embedding.is_not(None))
        ).all()
        if not rows:
            print("❌ Brak przykładów z embeddingami")
            return
        candidates = [(r.id, bytes_to_vec(r.embedding)) for r in rows]
        top = find_top_k(query_vec, candidates, k=args.k)
        print(f"\n📊 Top {len(top)} podobnych (z {len(rows)} przykładów):\n")
        for rid, score in top:
            ex = next(r for r in rows if r.id == rid)
            print(f"  [{score:.3f}] → {ex.to_email or '?'} | {ex.subject or '(bez tematu)'}")
            preview = (ex.body_text or "")[:200].replace("\n", " ")
            print(f"          {preview}...")
            print()


def main():
    p = argparse.ArgumentParser(prog="mail-dashboard")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("init-db")
    sub.add_parser("gen-fernet-key")
    sub.add_parser("list-accounts")

    pi = sub.add_parser("add-imap")
    pi.add_argument("email")
    pi.add_argument("host")
    pi.add_argument("port", type=int)
    pi.add_argument("smtp_host")
    pi.add_argument("smtp_port", type=int)
    pi.add_argument("--label")
    pi.add_argument("--password-file", help="Plik z PASSWORD=... (nie interaktywne)")

    pg = sub.add_parser("add-gmail")
    pg.add_argument("email")
    pg.add_argument("--credentials", required=True, help="Ścieżka do oauth-credentials.json")
    pg.add_argument("--label")
    pg.add_argument("--port", type=int, default=0,
                    help="Port lokalny dla OAuth callback (0=auto)")

    pt = sub.add_parser("test-account")
    pt.add_argument("email")

    pf = sub.add_parser("fetch-now")
    pf.add_argument("email")

    pcs = sub.add_parser("count-sent")
    pcs.add_argument("email")

    pis = sub.add_parser("import-sent")
    pis.add_argument("email")
    pis.add_argument("--limit", type=int, default=200)
    pis.add_argument("--reset", action="store_true", help="Usuń stare przykłady przed importem")
    pis.add_argument("--no-embeddings", action="store_true",
                     help="Tylko zapis surowy, bez embeddingów (oszczędność API)")

    pfs = sub.add_parser("find-similar")
    pfs.add_argument("query", help="Tekst zapytania (np. fragment maila klienta)")
    pfs.add_argument("--k", type=int, default=3)

    args = p.parse_args()

    if args.cmd == "init-db":
        _cmd_init_db()
    elif args.cmd == "gen-fernet-key":
        _cmd_gen_fernet()
    elif args.cmd == "add-imap":
        _cmd_add_imap(args)
    elif args.cmd == "add-gmail":
        _cmd_add_gmail(args)
    elif args.cmd == "test-account":
        _cmd_test_account(args)
    elif args.cmd == "list-accounts":
        _cmd_list_accounts()
    elif args.cmd == "fetch-now":
        _cmd_fetch_now(args)
    elif args.cmd == "count-sent":
        _cmd_count_sent(args)
    elif args.cmd == "import-sent":
        _cmd_import_sent(args)
    elif args.cmd == "find-similar":
        _cmd_find_similar(args)
    else:
        p.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
