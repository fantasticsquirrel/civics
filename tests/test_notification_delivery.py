from civics_app.db import connect, init_db, utcnow
from civics_app.notification_delivery import deliver_notifications


class FakeDelivery:
    def __init__(self, ready=True, error=False):
        self.ready = ready
        self.error = error
        self.sent = []
    def send(self, destination, subject, body):
        if self.error:
            raise RuntimeError("secret provider detail")
        self.sent.append((destination, subject, body))


def seed_notification(db_path, monkeypatch, channel="email"):
    monkeypatch.setenv("CIVICS_DB", str(db_path))
    init_db()
    with connect() as db:
        db.execute("INSERT INTO accounts(id,name,created_at) VALUES (1,'A',?)", (utcnow(),))
        db.execute("""INSERT INTO users(id,account_id,email,role,notification_email,telegram_chat_id,created_at)
                      VALUES (1,1,'u@example.test','user','notify@example.test','123',?)""", (utcnow(),))
        db.execute("""INSERT INTO bills(id,canonical_key,jurisdiction_kind,jurisdiction_code,session,chamber,bill_number,title,summary,status,source_name,source_url,text_url,introduced_at,updated_at,text_hash)
                      VALUES (1,'x','state','MO','1','House','HB 1','Bill','Summary','New','Source','https://e.test','https://e.test','','','h')""")
        db.execute("INSERT INTO categories(id,slug,name,description,created_at) VALUES (1,'education','Education','Schools',?)", (utcnow(),))
        db.execute("INSERT INTO bill_versions(id,bill_id,text_hash,source_url,text_url,created_at) VALUES (1,1,'h','https://e.test','https://e.test',?)", (utcnow(),))
        db.execute("INSERT INTO audit_runs(id,bill_id,bill_version_id,taxonomy_version,prompt_version,provider,status,created_at) VALUES (1,1,1,'t','p','x','completed',?)", (utcnow(),))
        db.execute("INSERT INTO bill_user_matches(id,user_id,bill_id,audit_run_id,category_id,created_at) VALUES (1,1,1,1,1,?)", (utcnow(),))
        db.execute("INSERT INTO notifications(user_id,match_id,channel,title,body,status,created_at) VALUES (1,1,?,'Title','Body','queued',?)", (channel, utcnow()))


def test_unconfigured_delivery_is_safe(tmp_path, monkeypatch):
    seed_notification(tmp_path / "db.sqlite", monkeypatch)
    result = deliver_notifications(smtp=FakeDelivery(ready=False), telegram=FakeDelivery(ready=False))
    assert result["not_configured"] == 1


def test_delivery_marks_success_and_redacts_failures(tmp_path, monkeypatch):
    seed_notification(tmp_path / "db.sqlite", monkeypatch)
    sender = FakeDelivery()
    assert deliver_notifications(smtp=sender, telegram=FakeDelivery())["delivered"] == 1
    assert sender.sent[0][0] == "notify@example.test"

    with connect() as db:
        db.execute("UPDATE notifications SET status='queued',delivered_at=NULL")
    assert deliver_notifications(smtp=FakeDelivery(error=True), telegram=FakeDelivery())["failed"] == 1
    with connect() as db:
        error = db.execute("SELECT last_error FROM notifications").fetchone()[0]
    assert "secret" not in error


def test_daily_digest_waits_until_due(tmp_path, monkeypatch):
    seed_notification(tmp_path / "db.sqlite", monkeypatch)
    with connect() as db:
        db.execute("UPDATE notifications SET status='digest_pending'")
        db.execute("""INSERT INTO notification_preferences(user_id,digest_frequency,channels,created_at,updated_at)
                      VALUES (1,'daily','[\"email\"]',?,?)""", (utcnow(), utcnow()))
    sender = FakeDelivery()
    assert deliver_notifications(smtp=sender, telegram=FakeDelivery())["selected"] == 0
    with connect() as db:
        db.execute("UPDATE notifications SET created_at='2000-01-01T00:00:00+00:00'")
    assert deliver_notifications(smtp=sender, telegram=FakeDelivery())["delivered"] == 1
