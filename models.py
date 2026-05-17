from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = "users"
    id            = db.Column(db.Integer, primary_key=True)
    email         = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255))
    google_id     = db.Column(db.String(255), unique=True)
    name          = db.Column(db.String(255))
    plan          = db.Column(db.String(50), default="free")
    created_at    = db.Column(db.DateTime, server_default=db.func.now())
    library       = db.relationship(
        "FilmLibrary", uselist=False, back_populates="user", cascade="all, delete-orphan"
    )

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return bool(self.password_hash and check_password_hash(self.password_hash, pw))


class FilmLibrary(db.Model):
    __tablename__ = "film_libraries"
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False)
    films_json = db.Column(db.Text, default="[]")
    has_dates  = db.Column(db.Boolean, default=False)
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())
    user       = db.relationship("User", back_populates="library")
