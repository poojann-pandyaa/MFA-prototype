"""Fake bank directory and demo personas. Everything here is invented; no real accounts, names or brands."""
import datetime
import math
import random

from app import config
from app.models import Account, Directory, Payment, Profile, User, UserAuthMethod

# (vpa, registered name, short label, age_days, fan_in_24h, merchant, risky_category, fri, complaints, similar_to)
DIRECTORY = [
    ("priya.sharma@okaxis", "Priya Sharma", "Friend (known)", 900, 1, False, False, 0, 0, None),
    ("rahul.verma@ybl", "Rahul Verma", "Friend (known)", 600, 2, False, False, 0, 0, None),
    ("mom.home@oksbi", "Sunita Devi", "Family (known)", 1500, 1, False, False, 0, 0, None),
    ("kirana.store@paytm", "Gupta Kirana Store", "Local shop", 1200, 85, True, False, 0, 0, None),
    ("citycab@ybl", "CityCab Services", "Taxi service", 800, 140, True, False, 0, 0, None),
    ("electricity.board@upi", "State Electricity Board", "Electricity bill", 2000, 500, True, False, 0, 0, None),
    ("bakery.joy@ybl", "Joy Bakery", "New local shop (genuine)", 45, 12, True, False, 0, 0, None),
    ("refund.desk@ybl", "R Desk Services", "Scam: fake customer-care refund", 2, 22, False, False, 2, 2, None),
    ("invest.profit99@ybl", "Profit Growth Trading", "Scam: investment / task (new, unreported)", 3, 3, True, True, 0, 0, None),
    ("priya.sharrna@okaxis", "P Sharma", "Scam: look-alike of Priya", 3, 14, False, False, 1, 0, "priya.sharma@okaxis"),
    ("quickseller.deals@ybl", "Quick Seller Deals", "Scam: fake seller (not yet reported)", 20, 9, True, False, 0, 0, None),
    ("officer.verify@upi", "Verification Cell", "Scam: fake police officer (new, unreported)", 3, 3, False, False, 0, 0, None),
    ("lucky.draw.prize@ybl", "Lucky Draw Fee", "Scam: prize fee", 30, 8, False, False, 0, 0, None),
]

# name: (story, median payment, number of past payments, hour range (IST), payee vpas, balance)
PERSONAS = {
    "asha": ("Busy professional, pays often, mostly by day.", 400, 70, (10, 20), ["priya.sharma@okaxis", "rahul.verma@ybl", "kirana.store@paytm", "citycab@ybl", "electricity.board@upi"], 60000),
    "ramesh": ("Retired, pays rarely and small amounts.", 250, 14, (9, 17), ["mom.home@oksbi", "kirana.store@paytm", "electricity.board@upi"], 150000),
    "kabir": ("Freelancer, pays late at night.", 600, 50, (22, 26), ["rahul.verma@ybl", "citycab@ybl", "kirana.store@paytm"], 80000),
    "meera": ("Brand-new user with almost no history.", 300, 3, (11, 19), ["priya.sharma@okaxis", "kirana.store@paytm"], 40000),
}


def seed_directory(db) -> None:
    """Insert or refresh the demo directory (safe to run on every start)."""
    for vpa, name, label, age, fan, merchant, risky, fri, compl, sim in DIRECTORY:
        db.merge(Directory(vpa=vpa, registered_name=name, label=label, age_days=age, fan_in_24h=fan, is_merchant=merchant,
                         risky_category=risky, fri=fri, complaints=compl, similar_to=sim))
    db.commit()


def load_persona(db, user, device_id: str, name: str) -> dict:
    """Wipe the user's payments and give them a history that looks like this persona. Deterministic for a given persona."""
    story, median, n, (h0, h1), payees, balance = PERSONAS[name]
    db.query(Payment).filter(Payment.user_id == user.id).delete()
    rng = random.Random(f"demopay-{name}")
    now = datetime.datetime.utcnow()
    for _ in range(n):
        days = rng.uniform(1, 60)
        hour = rng.uniform(h0, h1) % 24                                  # IST hour
        t = (now - datetime.timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
        t = t + datetime.timedelta(hours=hour) - datetime.timedelta(hours=5, minutes=30)
        if t > now:
            t -= datetime.timedelta(days=1)
        db.add(Payment(user_id=user.id, payee_vpa=rng.choice(payees), amount=round(median * math.exp(rng.gauss(0, 0.6)), 2), channel="pay",
                       device_id=device_id, created_at=t, status="completed", severity="LOW", threat="NONE", score=0.0, plan_json="{}",
                       steps_done=0, completed_at=t))
    acct = db.query(Account).filter(Account.user_id == user.id).first()
    acct.balance = float(balance)
    db.commit()
    return {"persona": name, "story": story, "payments": n, "balance": acct.balance}


# Pre-registered customers (invented). Username = persona name, so load_persona gives each their own history.
CUSTOMERS = {
    "asha": ("Asha Menon", 34, "Bengaluru", "Software engineer", "+91 90000 00001"),
    "ramesh": ("Ramesh Iyer", 68, "Pune", "Retired teacher", "+91 90000 00002"),
    "kabir": ("Kabir Khan", 29, "Mumbai", "Freelance designer", "+91 90000 00003"),
    "meera": ("Meera Nair", 22, "Kochi", "Student, new to digital payments", "+91 90000 00004"),
}


def seed_customers(db) -> None:
    """Create the four demo customers once (with their payment history). Later starts leave them, and any changes made, alone."""
    import bcrypt
    from authn.registry import UNIVERSAL_IMPLEMENTED
    for name, (full, age, city, job, mobile) in CUSTOMERS.items():
        if db.query(User).filter(User.username == name).first():
            continue
        user = User(username=name, password_hash=bcrypt.hashpw(config.DEMO_PASSWORD.encode(), bcrypt.gensalt()).decode())
        db.add(user)
        db.commit()
        db.refresh(user)
        db.add(Account(user_id=user.id, balance=0, number=f"5010{user.id:08d}", ifsc="DEMO0000001", kind="Savings"))
        db.add(Profile(user_id=user.id, full_name=full, age=age, city=city, occupation=job, mobile=mobile, story=PERSONAS[name][0]))
        for m in UNIVERSAL_IMPLEMENTED:
            db.add(UserAuthMethod(user_id=user.id, method=m))
        db.commit()
        load_persona(db, user, "web-device", name)
