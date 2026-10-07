"""
Datenbankmodelle für Seitentausch.

Matching-Prinzip:
- Ein User "liked" ein Buch eines anderen Users (Like).
- Sobald User A ein Buch von User B geliked hat UND User B (umgekehrt)
  irgendein Buch von User A geliked hat, entsteht ein Match zwischen
  User A und User B (siehe app.py -> check_for_match()).
- Zu jedem Match gehört ein Chat-Verlauf (Message).
"""
from datetime import datetime, timezone
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from extensions import db


def utcnow():
    return datetime.now(timezone.utc)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    city = db.Column(db.String(120), nullable=True)
    avatar_color = db.Column(db.String(7), default="#2F4B3C")
    created_at = db.Column(db.DateTime, default=utcnow)

    books = db.relationship(
        "Book", backref="owner", lazy="dynamic", cascade="all, delete-orphan"
    )
    likes = db.relationship(
        "Like", backref="user", lazy="dynamic", cascade="all, delete-orphan"
    )

    def set_password(self, raw_password):
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        return check_password_hash(self.password_hash, raw_password)

    def __repr__(self):
        return f"<User {self.email}>"


class Book(db.Model):
    __tablename__ = "books"

    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    author = db.Column(db.String(255), nullable=False)
    condition = db.Column(db.String(50), nullable=False)  # Neuwertig/Sehr gut/Gut/Ordentlich
    description = db.Column(db.Text, nullable=True)
    cover_image_url = db.Column(db.String(500), nullable=True)  # Cloudinary-URL
    cloudinary_public_id = db.Column(db.String(255), nullable=True)
    is_active = db.Column(db.Boolean, default=True)  # false = getauscht/entfernt
    created_at = db.Column(db.DateTime, default=utcnow)

    likes = db.relationship(
        "Like", backref="book", lazy="dynamic", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<Book {self.title!r}>"


class Like(db.Model):
    """Ein User liked ein bestimmtes Buch eines anderen Users."""
    __tablename__ = "likes"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    book_id = db.Column(db.Integer, db.ForeignKey("books.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)

    __table_args__ = (
        db.UniqueConstraint("user_id", "book_id", name="uq_like_user_book"),
    )


class Pass(db.Model):
    """Ein User hat ein Buch explizit weggewischt (nicht interessiert)."""
    __tablename__ = "passes"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    book_id = db.Column(db.Integer, db.ForeignKey("books.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)

    __table_args__ = (
        db.UniqueConstraint("user_id", "book_id", name="uq_pass_user_book"),
    )


class Match(db.Model):
    """Entsteht, wenn zwei User sich gegenseitig für je ein Buch interessieren."""
    __tablename__ = "matches"

    id = db.Column(db.Integer, primary_key=True)
    user1_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    user2_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    # Die konkreten Bücher, die den Match ausgelöst haben (fürs Anzeigen im Chat)
    book1_id = db.Column(db.Integer, db.ForeignKey("books.id"), nullable=False)
    book2_id = db.Column(db.Integer, db.ForeignKey("books.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)

    user1 = db.relationship("User", foreign_keys=[user1_id])
    user2 = db.relationship("User", foreign_keys=[user2_id])
    book1 = db.relationship("Book", foreign_keys=[book1_id])
    book2 = db.relationship("Book", foreign_keys=[book2_id])
    messages = db.relationship(
        "Message", backref="match", lazy="dynamic",
        cascade="all, delete-orphan", order_by="Message.created_at",
    )

    __table_args__ = (
        db.UniqueConstraint("user1_id", "user2_id", name="uq_match_users"),
    )

    def other_user(self, current_user_id):
        return self.user2 if self.user1_id == current_user_id else self.user1

    def my_book(self, current_user_id):
        return self.book1 if self.user1_id == current_user_id else self.book2

    def their_book(self, current_user_id):
        return self.book2 if self.user1_id == current_user_id else self.book1


class Message(db.Model):
    __tablename__ = "messages"

    id = db.Column(db.Integer, primary_key=True)
    match_id = db.Column(db.Integer, db.ForeignKey("matches.id"), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)

    sender = db.relationship("User")
