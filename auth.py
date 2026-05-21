import os
from flask import Blueprint, render_template, redirect, url_for, request, jsonify
from flask_login import login_user, logout_user, login_required, current_user
from authlib.integrations.flask_client import OAuth
from models import db, User

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")
oauth   = OAuth()


def init_auth(app):
    oauth.init_app(app)
    oauth.register(
        name="google",
        client_id=app.config.get("GOOGLE_CLIENT_ID"),
        client_secret=app.config.get("GOOGLE_CLIENT_SECRET"),
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        if request.is_json:
            return jsonify({"ok": True})
        return redirect(url_for("welcome"))
    if request.method == "POST":
        if request.is_json:
            data = request.get_json()
            email    = data.get("email", "").strip().lower()
            password = data.get("password", "")
            name     = data.get("name", "").strip()
        else:
            email    = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            name     = request.form.get("name", "").strip()
        if not email or not password:
            error = "Email and password are required."
        elif User.query.filter_by(email=email).first():
            error = "An account with that email already exists."
        else:
            user = User(email=email, name=name)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            login_user(user)
            if request.is_json:
                return jsonify({"ok": True})
            return redirect(url_for("welcome"))
        if request.is_json:
            return jsonify({"error": error})
        return render_template("auth/register.html", error=error)
    return render_template("auth/register.html", error=None)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        if request.is_json:
            return jsonify({"ok": True})
        return redirect(url_for("welcome"))
    if request.method == "POST":
        if request.is_json:
            data = request.get_json()
            email    = data.get("email", "").strip().lower()
            password = data.get("password", "")
        else:
            email    = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()
        if not user or not user.check_password(password):
            error = "Invalid email or password."
            if request.is_json:
                return jsonify({"error": error})
            return render_template("auth/login.html", error=error)
        login_user(user)
        if request.is_json:
            return jsonify({"ok": True})
        next_url = request.args.get("next") or url_for("welcome")
        return redirect(next_url)
    return render_template("auth/login.html", error=None)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("index"))


@auth_bp.route("/google")
def google_login():
    redirect_uri = url_for("auth.google_callback", _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@auth_bp.route("/google/callback")
def google_callback():
    token     = oauth.google.authorize_access_token()
    user_info = token.get("userinfo")
    if not user_info:
        return redirect(url_for("auth.login"))
    email     = user_info["email"]
    google_id = user_info["sub"]
    name      = user_info.get("name", "")
    user = User.query.filter_by(google_id=google_id).first()
    if not user:
        user = User.query.filter_by(email=email).first()
        if user:
            user.google_id = google_id
        else:
            user = User(email=email, name=name, google_id=google_id)
            db.session.add(user)
    db.session.commit()
    login_user(user)
    return redirect(url_for("welcome"))
