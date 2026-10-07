"""
Seitentausch – Tausch-App für Illustrationsbücher.

Struktur bewusst analog zur bestehenden Buchhandlung gehalten:
app.py (Routen) / models.py (DB) / extensions.py (db, login, socketio)
/templates, /static
"""
import os
from datetime import datetime, timezone

import cloudinary
import cloudinary.uploader
from flask import (
    Flask, render_template, redirect, url_for, flash,
    request, jsonify, abort,
)
from flask_login import (
    login_user, logout_user, login_required, current_user,
)
from flask_socketio import join_room, emit
from sqlalchemy import or_, and_
from sqlalchemy.exc import IntegrityError

from extensions import db, login_manager, socketio
from models import User, Book, Like, Pass, Match, Message, utcnow

CONDITIONS = ["Neuwertig", "Sehr gut", "Gut", "Ordentlich"]
AVATAR_COLORS = ["#2F4B3C", "#B8863B", "#7A3E3E", "#3E5A7A", "#6B5B95", "#4A6B5A"]


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")

    db_url = os.environ.get("DATABASE_URL", "sqlite:///seitentausch.db")
    # Render liefert "postgres://", SQLAlchemy 2.x will "postgresql://"
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = db_url
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}

    cloudinary.config(
        cloud_name=os.environ.get("CLOUDINARY_CLOUD_NAME"),
        api_key=os.environ.get("CLOUDINARY_API_KEY"),
        api_secret=os.environ.get("CLOUDINARY_API_SECRET"),
        secure=True,
    )

    db.init_app(app)
    login_manager.init_app(app)
    socketio.init_app(app, message_queue=os.environ.get("REDIS_URL"))

    register_routes(app)
    register_socketio_events()

    with app.app_context():
        db.create_all()

    return app


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# ---------------------------------------------------------------------------
# Matching-Logik
# ---------------------------------------------------------------------------

def check_for_match(liking_user: User, liked_book: Book):
    """
    Prüft, ob durch das neue Like ein Match entsteht:
    liking_user hat liked_book (Besitzer: owner) geliked.
    Match entsteht, wenn owner umgekehrt auch irgendein Buch von
    liking_user geliked hat, das owner noch gehört (ist noch aktiv).
    """
    owner = liked_book.owner
    if owner.id == liking_user.id:
        return None

    reverse_like = (
        db.session.query(Like)
        .join(Book, Like.book_id == Book.id)
        .filter(
            Like.user_id == owner.id,
            Book.owner_id == liking_user.id,
            Book.is_active.is_(True),
        )
        .first()
    )
    if not reverse_like:
        return None

    their_book = db.session.get(Book, reverse_like.book_id)

    user1_id, user2_id = sorted([liking_user.id, owner.id])
    existing = Match.query.filter_by(user1_id=user1_id, user2_id=user2_id).first()
    if existing:
        return None

    if user1_id == liking_user.id:
        book1_id, book2_id = their_book.id, liked_book.id
    else:
        book1_id, book2_id = liked_book.id, their_book.id

    match = Match(
        user1_id=user1_id, user2_id=user2_id,
        book1_id=book1_id, book2_id=book2_id,
    )
    db.session.add(match)
    db.session.commit()
    return match


# ---------------------------------------------------------------------------
# Routen
# ---------------------------------------------------------------------------

def register_routes(app):

    @app.context_processor
    def inject_globals():
        pending_matches = 0
        if current_user.is_authenticated:
            pending_matches = Match.query.filter(
                or_(Match.user1_id == current_user.id, Match.user2_id == current_user.id)
            ).count()
        return dict(pending_matches=pending_matches)

    @app.route("/")
    def index():
        if current_user.is_authenticated:
            return redirect(url_for("discover"))
        return render_template("index.html")

    # -- Auth -----------------------------------------------------------

    @app.route("/registrieren", methods=["GET", "POST"])
    def register():
        if current_user.is_authenticated:
            return redirect(url_for("discover"))
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            city = request.form.get("city", "").strip()

            if not name or not email or len(password) < 8:
                flash("Bitte Name, E-Mail ausfüllen und ein Passwort mit mind. 8 Zeichen wählen.", "error")
                return render_template("register.html")

            if User.query.filter_by(email=email).first():
                flash("Für diese E-Mail existiert bereits ein Konto.", "error")
                return render_template("register.html")

            user = User(
                name=name, email=email, city=city or None,
                avatar_color=AVATAR_COLORS[User.query.count() % len(AVATAR_COLORS)],
            )
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            login_user(user)
            flash(f"Willkommen, {user.name}! Lade jetzt dein erstes Buch hoch.", "success")
            return redirect(url_for("add_book"))

        return render_template("register.html")

    @app.route("/anmelden", methods=["GET", "POST"])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for("discover"))
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            user = User.query.filter_by(email=email).first()
            if user and user.check_password(password):
                login_user(user, remember=True)
                return redirect(url_for("discover"))
            flash("E-Mail oder Passwort stimmt nicht.", "error")
        return render_template("login.html")

    @app.route("/abmelden")
    @login_required
    def logout():
        logout_user()
        return redirect(url_for("index"))

    # -- Meine Bücher -----------------------------------------------------

    @app.route("/meine-buecher")
    @login_required
    def my_books():
        books = (
            Book.query.filter_by(owner_id=current_user.id, is_active=True)
            .order_by(Book.created_at.desc())
            .all()
        )
        return render_template("my_books.html", books=books)

    @app.route("/buch-hinzufuegen", methods=["GET", "POST"])
    @login_required
    def add_book():
        if request.method == "POST":
            title = request.form.get("title", "").strip()
            author = request.form.get("author", "").strip()
            condition = request.form.get("condition", CONDITIONS[2])
            description = request.form.get("description", "").strip()
            photo = request.files.get("photo")

            if not title or not author:
                flash("Titel und Autor:in werden benötigt.", "error")
                return render_template("add_book.html", conditions=CONDITIONS)

            cover_url, public_id = None, None
            if photo and photo.filename:
                try:
                    result = cloudinary.uploader.upload(
                        photo,
                        folder="seitentausch/buecher",
                        transformation=[{"width": 800, "height": 1100, "crop": "fill"}],
                    )
                    cover_url = result.get("secure_url")
                    public_id = result.get("public_id")
                except Exception as exc:  # noqa: BLE001
                    flash(f"Foto-Upload fehlgeschlagen: {exc}", "error")
                    return render_template("add_book.html", conditions=CONDITIONS)

            book = Book(
                owner_id=current_user.id,
                title=title,
                author=author,
                condition=condition,
                description=description or None,
                cover_image_url=cover_url,
                cloudinary_public_id=public_id,
            )
            db.session.add(book)
            db.session.commit()
            flash("Buch hochgeladen!", "success")
            return redirect(url_for("my_books"))

        return render_template("add_book.html", conditions=CONDITIONS)

    @app.route("/buch/<int:book_id>/loeschen", methods=["POST"])
    @login_required
    def delete_book(book_id):
        book = db.session.get(Book, book_id)
        if not book or book.owner_id != current_user.id:
            abort(404)
        if book.cloudinary_public_id:
            try:
                cloudinary.uploader.destroy(book.cloudinary_public_id)
            except Exception:  # noqa: BLE001
                pass
        db.session.delete(book)
        db.session.commit()
        flash("Buch entfernt.", "success")
        return redirect(url_for("my_books"))

    # -- Entdecken / Swipe --------------------------------------------------

    @app.route("/entdecken")
    @login_required
    def discover():
        seen_book_ids = db.session.query(Like.book_id).filter_by(user_id=current_user.id)
        passed_book_ids = db.session.query(Pass.book_id).filter_by(user_id=current_user.id)

        book = (
            Book.query.filter(
                Book.owner_id != current_user.id,
                Book.is_active.is_(True),
                ~Book.id.in_(seen_book_ids),
                ~Book.id.in_(passed_book_ids),
            )
            .order_by(Book.created_at.desc())
            .first()
        )
        remaining = (
            Book.query.filter(
                Book.owner_id != current_user.id,
                Book.is_active.is_(True),
                ~Book.id.in_(seen_book_ids),
                ~Book.id.in_(passed_book_ids),
            ).count()
        )
        return render_template("discover.html", book=book, remaining=remaining)

    @app.route("/swipe/<int:book_id>/<action>", methods=["POST"])
    @login_required
    def swipe(book_id, action):
        if action not in ("like", "pass"):
            abort(400)
        book = db.session.get(Book, book_id)
        if not book or book.owner_id == current_user.id:
            abort(404)

        match = None
        if action == "like":
            if not Like.query.filter_by(user_id=current_user.id, book_id=book.id).first():
                db.session.add(Like(user_id=current_user.id, book_id=book.id))
                db.session.commit()
                match = check_for_match(current_user, book)
        else:
            if not Pass.query.filter_by(user_id=current_user.id, book_id=book.id).first():
                db.session.add(Pass(user_id=current_user.id, book_id=book.id))
                db.session.commit()

        return jsonify({
            "ok": True,
            "match": bool(match),
            "match_id": match.id if match else None,
        })

    # -- Matches & Chat ------------------------------------------------------

    @app.route("/matches")
    @login_required
    def matches():
        user_matches = (
            Match.query.filter(
                or_(Match.user1_id == current_user.id, Match.user2_id == current_user.id)
            )
            .order_by(Match.created_at.desc())
            .all()
        )
        data = []
        for m in user_matches:
            other = m.other_user(current_user.id)
            last_msg = m.messages.order_by(Message.created_at.desc()).first()
            data.append({"match": m, "other": other, "last_message": last_msg})
        return render_template("matches.html", matches=data)

    @app.route("/chat/<int:match_id>")
    @login_required
    def chat(match_id):
        match = db.session.get(Match, match_id)
        if not match or current_user.id not in (match.user1_id, match.user2_id):
            abort(404)
        other = match.other_user(current_user.id)
        history = match.messages.all()
        return render_template("chat.html", match=match, other=other, history=history)


# ---------------------------------------------------------------------------
# Live-Chat über Socket.IO
# ---------------------------------------------------------------------------

def register_socketio_events():

    @socketio.on("join_match")
    def on_join(data):
        match_id = data.get("match_id")
        match = db.session.get(Match, int(match_id)) if match_id else None
        if not match or not current_user.is_authenticated:
            return
        if current_user.id not in (match.user1_id, match.user2_id):
            return
        join_room(f"match_{match_id}")

    @socketio.on("send_message")
    def on_send_message(data):
        if not current_user.is_authenticated:
            return
        match_id = data.get("match_id")
        content = (data.get("content") or "").strip()
        if not content or not match_id:
            return

        match = db.session.get(Match, int(match_id))
        if not match or current_user.id not in (match.user1_id, match.user2_id):
            return

        message = Message(match_id=match.id, sender_id=current_user.id, content=content[:2000])
        db.session.add(message)
        db.session.commit()

        emit(
            "new_message",
            {
                "id": message.id,
                "match_id": match.id,
                "sender_id": current_user.id,
                "sender_name": current_user.name,
                "content": message.content,
                "created_at": message.created_at.replace(tzinfo=timezone.utc).isoformat(),
            },
            room=f"match_{match_id}",
        )


app = create_app()

if __name__ == "__main__":
    socketio.run(app, debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
