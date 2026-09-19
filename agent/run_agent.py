        """create table if not exists tasks(
            id integer primary key, created text, status text, title text, detail text)"""
    )
    c.execute(
        """create table if not exists lead_state(
            email text primary key,
            first_seen text,
            last_seen text,
            replied text default '',
            last_outbound text default '',
            last_inbound text default '',
            followup_step integer default 0,
            next_action_at text default '',
            status text default 'new',
            intent text default '',
            need text default '',
            objections text default '',
            customer_id text default '',
            subscription_id text default '',
            checkout_session_id text default '',
            paid_at text default '',
            activated_at text default '',
            last_payment_at text default '',
            current_period_end text default '',
            last_feedback_at text default '',
            last_support_at text default '',
            opted_out integer default 0,
            last_name text default '',
            agency text default '',
            source text default 'brevo')"""
    )
    # Backward-compatible migrations for an existing local SQLite database.
    required_columns = {
        "replied": "text default ''",
        "last_outbound": "text default ''",
        "last_inbound": "text default ''",
        "followup_step": "integer default 0",
        "next_action_at": "text default ''",
        "status": "text default 'new'",
        "intent": "text default ''",
        "need": "text default ''",
        "objections": "text default ''",
        "customer_id": "text default ''",
        "subscription_id": "text default ''",
        "checkout_session_id": "text default ''",
        "paid_at": "text default ''",
        "activated_at": "text default ''",
        "last_payment_at": "text default ''",
        "current_period_end": "text default ''",
        "last_feedback_at": "text default ''",
        "last_support_at": "text default ''",
        "opted_out": "integer default 0",
        "last_name": "text default ''",
        "agency": "text default ''",
        "source": "text default 'brevo'",
    }
    existing = {row[1] for row in c.execute("pragma table_info(lead_state)").fetchall()}
    for column, definition in required_columns.items():
        if column not in existing:
            c.execute(f"alter table lead_state add column {column} {definition}")
    c.commit()
    return c


def log(kind, data):
    c = db()
    c.execute(
        "insert into events(ts,kind,data) values(?,?,?)",
        (iso(), kind, json.dumps(data, ensure_ascii=False, default=str)),
    )