import os
import re
import secrets
import smtplib
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from functools import wraps

from mailer import send_code_email
from dotenv import load_dotenv
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

app = Flask(__name__)
app.secret_key = os.environ.get(
    "ANIVERSE_SECRET_KEY",
    "anime-review-secret-key-change-this",
)

DB_PATH = os.environ.get("ANIVERSE_DB_PATH", os.path.join(BASE_DIR, "anime.db"))
UPLOAD_FOLDER = os.environ.get("ANIVERSE_UPLOAD_FOLDER", os.path.join(BASE_DIR, "uploads"))

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
CODE_EXPIRE_MINUTES = 5
CODE_COOLDOWN_SECONDS = 60
SITE_TIMEZONE = timezone(timedelta(hours=8), "Asia/Shanghai")


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )


def ensure_column(db, table, column, definition):
    columns = db.execute(f"PRAGMA table_info({table})").fetchall()
    column_names = {row["name"] for row in columns}

    if column not in column_names:
        db.execute(
            f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
        )


def init_db():
    db = get_db()
    db.execute("PRAGMA journal_mode = WAL")

    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS anime (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mal_id INTEGER UNIQUE,
            title TEXT NOT NULL,
            title_japanese TEXT,
            description TEXT,
            cover TEXT,
            status TEXT DEFAULT 'airing',
            mal_score REAL,
            year INTEGER,
            genres TEXT,
            source_url TEXT
        );

        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            anime_id INTEGER NOT NULL,
            rating INTEGER NOT NULL,
            content TEXT NOT NULL,
            image TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(anime_id) REFERENCES anime(id)
        );

        CREATE TABLE IF NOT EXISTS email_codes (
            email TEXT PRIMARY KEY,
            code_hash TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            sent_at TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS forum_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            views INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS forum_replies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(post_id) REFERENCES forum_posts(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        """
    )

    db.execute("""
        CREATE TABLE IF NOT EXISTS email_send_locks (
            email TEXT PRIMARY KEY,
            request_id TEXT NOT NULL,
            started_at TEXT NOT NULL
        )
    """)
    ensure_column(db, "anime", "title_zh", "TEXT")
    ensure_column(db, "anime", "is_classic", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(db, "anime", "popularity", "INTEGER NOT NULL DEFAULT 0")
    db.execute("CREATE INDEX IF NOT EXISTS idx_anime_classic_popularity ON anime(is_classic, popularity DESC)")

    # 对旧数据库增量升级，不删除已有用户、番剧、评论。
    ensure_column(db, "users", "email", "TEXT")
    ensure_column(db, "users", "created_at", "DATETIME")

    ensure_column(db, "anime", "cover_small", "TEXT")
    ensure_column(db, "anime", "start_year", "INTEGER")
    ensure_column(db, "anime", "start_month", "INTEGER")
    ensure_column(db, "anime", "start_day", "INTEGER")
    ensure_column(db, "anime", "season", "TEXT")

    # 给旧数据补上可推导的季度字段；新的同步会用 AniList 的真实 season 覆盖。
    db.execute(
        """
        UPDATE anime
        SET season = CASE
            WHEN start_month BETWEEN 1 AND 3 THEN 'WINTER'
            WHEN start_month BETWEEN 4 AND 6 THEN 'SPRING'
            WHEN start_month BETWEEN 7 AND 9 THEN 'SUMMER'
            WHEN start_month BETWEEN 10 AND 12 THEN 'FALL'
            ELSE season
        END
        WHERE season IS NULL AND start_month IS NOT NULL
        """
    )

    db.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_unique
        ON users(email)
        WHERE email IS NOT NULL
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_forum_posts_created_at ON forum_posts(created_at DESC)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_forum_replies_post_id ON forum_replies(post_id)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_anime_season_year ON anime(season, year)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_anime_start_date ON anime(start_year, start_month, start_day)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_anime_status ON anime(status)"
    )

    db.commit()
    db.close()


def current_season_info():
    """Return AniList season key, year, Chinese label, and month range."""
    now = datetime.now(SITE_TIMEZONE)
    month = now.month

    if month <= 3:
        return "WINTER", now.year, "冬季", (1, 3)
    if month <= 6:
        return "SPRING", now.year, "春季", (4, 6)
    if month <= 9:
        return "SUMMER", now.year, "夏季", (7, 9)
    return "FALL", now.year, "秋季", (10, 12)


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if "user_id" not in session:
            flash("请先登录后继续访问 AniVerse。", "warning")
            return redirect(url_for("auth"))
        return view(*args, **kwargs)

    return wrapped_view


def normalize_email(value):
    return value.strip().lower() if isinstance(value, str) else ""


def utc_now():
    return datetime.now(timezone.utc)


def parse_iso_datetime(value):
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


@app.template_filter("beijing_time")
def beijing_time(value):
    dt = value if isinstance(value, datetime) else parse_iso_datetime(value)
    if dt is None:
        return "时间未知"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(SITE_TIMEZONE).strftime("%Y-%m-%d %H:%M")


@app.template_filter("utc_iso")
def utc_iso(value):
    dt = value if isinstance(value, datetime) else parse_iso_datetime(value)
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def send_verification_email(target_email, code):
    return send_code_email(target_email, code, CODE_EXPIRE_MINUTES)


# Waitress 通过 app:app 启动时也需要自动完成数据库升级。
init_db()


@app.route("/")
def auth():
    if "user_id" in session:
        return redirect(url_for("home"))

    mode = request.args.get("mode", "login")
    if mode not in {"login", "register"}:
        mode = "login"

    return render_template("auth.html", mode=mode)


@app.route("/auth/send-code", methods=["POST"])
def send_email_code():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        payload = request.form
    email = normalize_email(payload.get("email"))
    if not EMAIL_RE.match(email):
        return jsonify(ok=False, message="请输入有效的邮箱地址。"), 400

    now = utc_now()
    last_sent = parse_iso_datetime(session.get("last_code_sent_at"))
    if last_sent and (now - last_sent).total_seconds() < CODE_COOLDOWN_SECONDS:
        remaining = max(1, int(CODE_COOLDOWN_SECONDS - (now - last_sent).total_seconds()))
        return jsonify(ok=False, message=f"发送太频繁，请 {remaining} 秒后再试。", retry_after=remaining), 429

    request_id = uuid.uuid4().hex
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT id FROM users WHERE lower(email) = ?", (email,)).fetchone():
            return jsonify(ok=False, message="该邮箱已经注册。"), 409
        current = db.execute("SELECT sent_at FROM email_codes WHERE email = ?", (email,)).fetchone()
        sent_at = parse_iso_datetime(current["sent_at"]) if current else None
        if sent_at and (now - sent_at).total_seconds() < CODE_COOLDOWN_SECONDS:
            remaining = max(1, int(CODE_COOLDOWN_SECONDS - (now - sent_at).total_seconds()))
            return jsonify(ok=False, message=f"请 {remaining} 秒后再发送验证码。", retry_after=remaining), 429
        pending = db.execute("SELECT started_at FROM email_send_locks WHERE email = ?", (email,)).fetchone()
        started = parse_iso_datetime(pending["started_at"]) if pending else None
        if started and (now - started).total_seconds() < 120:
            return jsonify(ok=False, message="验证码正在发送，请稍候再试。", retry_after=10), 429
        db.execute("INSERT OR REPLACE INTO email_send_locks(email, request_id, started_at) VALUES (?, ?, ?)",
                   (email, request_id, now.isoformat()))
        db.commit()
    finally:
        db.close()

    code = f"{secrets.randbelow(1_000_000):06d}"
    try:
        metadata = send_verification_email(email, code) or {}
        sent_at = utc_now()
        db = get_db()
        try:
            db.execute("""
                INSERT INTO email_codes(email, code_hash, expires_at, sent_at, attempts)
                VALUES (?, ?, ?, ?, 0)
                ON CONFLICT(email) DO UPDATE SET code_hash=excluded.code_hash,
                    expires_at=excluded.expires_at, sent_at=excluded.sent_at, attempts=0
            """, (email, generate_password_hash(code),
                  (sent_at + timedelta(minutes=CODE_EXPIRE_MINUTES)).isoformat(), sent_at.isoformat()))
            db.commit()
        finally:
            db.close()
        session["last_code_sent_at"] = sent_at.isoformat()
        app.logger.warning("verification_send_accepted request_id=%s message_id=%s recipient_domain=%s",
                           request_id, metadata.get("message_id", "test"), email.rsplit("@", 1)[-1])
        return jsonify(ok=True, message=f"验证码邮件已提交发送到 {email}，{CODE_EXPIRE_MINUTES} 分钟内有效。若未看到，请检查垃圾邮件。",
                       cooldown=CODE_COOLDOWN_SECONDS)
    except Exception as exc:
        app.logger.error("verification_send_failed request_id=%s error_type=%s smtp_code=%s recipient_domain=%s",
                         request_id, type(exc).__name__, getattr(exc, "smtp_code", None), email.rsplit("@", 1)[-1])
        return jsonify(ok=False, message="验证码暂时发送失败，请稍后重试。", request_id=request_id), 503
    finally:
        db = get_db()
        try:
            db.execute("DELETE FROM email_send_locks WHERE email = ? AND request_id = ?", (email, request_id))
            db.commit()
        finally:
            db.close()


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return redirect(url_for("auth", mode="register"))

    username = request.form.get("username", "").strip()
    email = normalize_email(request.form.get("email"))
    code = request.form.get("code", "").strip()
    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")

    if len(username) < 3 or len(username) > 24:
        flash("用户名需要 3-24 个字符。", "danger")
        return redirect(url_for("auth", mode="register"))

    if not EMAIL_RE.match(email):
        flash("请输入有效的邮箱地址。", "danger")
        return redirect(url_for("auth", mode="register"))

    if not re.fullmatch(r"\d{6}", code):
        flash("请输入 6 位数字验证码。", "danger")
        return redirect(url_for("auth", mode="register"))

    if len(password) < 6:
        flash("密码至少需要 6 个字符。", "danger")
        return redirect(url_for("auth", mode="register"))

    if password != confirm_password:
        flash("两次输入的密码不一致。", "danger")
        return redirect(url_for("auth", mode="register"))

    db = get_db()

    duplicate = db.execute(
        """
        SELECT id FROM users
        WHERE username = ? OR lower(email) = lower(?)
        """,
        (username, email),
    ).fetchone()

    if duplicate:
        db.close()
        flash("用户名或邮箱已经被使用。", "danger")
        return redirect(url_for("auth", mode="register"))

    code_row = db.execute(
        "SELECT * FROM email_codes WHERE email = ?",
        (email,),
    ).fetchone()

    if not code_row:
        db.close()
        flash("请先获取邮箱验证码。", "danger")
        return redirect(url_for("auth", mode="register"))

    expires_at = parse_iso_datetime(code_row["expires_at"])

    if not expires_at or utc_now() > expires_at:
        db.execute("DELETE FROM email_codes WHERE email = ?", (email,))
        db.commit()
        db.close()
        flash("验证码已经过期，请重新发送。", "danger")
        return redirect(url_for("auth", mode="register"))

    if code_row["attempts"] >= 5:
        db.execute("DELETE FROM email_codes WHERE email = ?", (email,))
        db.commit()
        db.close()
        flash("验证码尝试次数过多，请重新发送。", "danger")
        return redirect(url_for("auth", mode="register"))

    if not check_password_hash(code_row["code_hash"], code):
        db.execute(
            "UPDATE email_codes SET attempts = attempts + 1 WHERE email = ?",
            (email,),
        )
        db.commit()
        db.close()
        flash("验证码错误。", "danger")
        return redirect(url_for("auth", mode="register"))

    try:
        cursor = db.execute(
            """
            INSERT INTO users(username, email, password, created_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (username, email, generate_password_hash(password)),
        )
        user_id = cursor.lastrowid
        db.execute("DELETE FROM email_codes WHERE email = ?", (email,))
        db.commit()
    except sqlite3.IntegrityError:
        db.close()
        flash("用户名或邮箱已经被使用。", "danger")
        return redirect(url_for("auth", mode="register"))

    db.close()

    session.clear()
    session["user_id"] = user_id
    session["username"] = username
    session["email"] = email

    flash("注册成功，欢迎来到 AniVerse！", "success")
    return redirect(url_for("home"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return redirect(url_for("auth", mode="login"))

    identity = request.form.get("identity", "").strip()
    password = request.form.get("password", "")

    db = get_db()
    user = db.execute(
        """
        SELECT * FROM users
        WHERE lower(email) = lower(?) OR username = ?
        LIMIT 1
        """,
        (identity, identity),
    ).fetchone()
    db.close()

    if user and check_password_hash(user["password"], password):
        session.clear()
        session["user_id"] = user["id"]
        session["username"] = user["username"]
        session["email"] = user["email"] if "email" in user.keys() else None
        return redirect(url_for("home"))

    flash("邮箱/用户名或密码错误。", "danger")
    return redirect(url_for("auth", mode="login"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth"))


@app.route("/home")
@login_required
def home():
    season_key, season_year, season_label, (start_month, end_month) = current_season_info()
    db = get_db()

    season_condition = """
        (
            (anime.season = ? AND anime.year = ?)
            OR
            (anime.start_year = ? AND anime.start_month BETWEEN ? AND ?)
        )
    """
    season_params = (
        season_key,
        season_year,
        season_year,
        start_month,
        end_month,
    )

    anime_list = db.execute(
        f"""
        SELECT
            anime.*,
            ROUND(AVG(reviews.rating), 1) AS user_score,
            COUNT(reviews.id) AS review_count,
            COALESCE(
                ROUND(AVG(reviews.rating), 1),
                anime.mal_score
            ) AS display_score
        FROM anime
        LEFT JOIN reviews ON anime.id = reviews.anime_id
        WHERE {season_condition}
        GROUP BY anime.id
        ORDER BY display_score DESC, review_count DESC, anime.id DESC
        LIMIT 12
        """,
        season_params,
    ).fetchall()

    community_top = db.execute(
        """
        SELECT
            anime.*,
            ROUND(AVG(reviews.rating), 1) AS user_score,
            COUNT(reviews.id) AS review_count
        FROM anime
        JOIN reviews ON anime.id = reviews.anime_id
        GROUP BY anime.id
        HAVING COUNT(reviews.id) > 0
        ORDER BY user_score DESC, review_count DESC, anime.id DESC
        LIMIT 6
        """
    ).fetchall()

    upcoming = db.execute(
        """
        SELECT
            anime.*,
            ROUND(AVG(reviews.rating), 1) AS user_score,
            COUNT(reviews.id) AS review_count,
            COALESCE(
                ROUND(AVG(reviews.rating), 1),
                anime.mal_score
            ) AS display_score
        FROM anime
        LEFT JOIN reviews ON anime.id = reviews.anime_id
        WHERE anime.status = 'upcoming'
        GROUP BY anime.id
        ORDER BY
            CASE WHEN anime.start_year IS NULL THEN 1 ELSE 0 END,
            anime.start_year ASC,
            anime.start_month ASC,
            CASE WHEN anime.start_day IS NULL THEN 1 ELSE 0 END,
            anime.start_day ASC,
            display_score DESC
        LIMIT 12
        """
    ).fetchall()

    classics = db.execute("""
        SELECT anime.*, ROUND(AVG(reviews.rating), 1) AS user_score,
            COUNT(reviews.id) AS review_count,
            COALESCE(ROUND(AVG(reviews.rating), 1), anime.mal_score) AS display_score
        FROM anime LEFT JOIN reviews ON anime.id = reviews.anime_id
        WHERE anime.is_classic = 1 GROUP BY anime.id
        ORDER BY anime.popularity DESC, display_score DESC LIMIT 10
    """).fetchall()
    db.close()

    return render_template(
        "index.html",
        anime_list=anime_list,
        community_top=community_top,
        upcoming=upcoming,
        classics=classics,
        season_label=season_label,
        season_year=season_year,
    )


@app.route("/anime-list")
@app.route("/season")
@login_required
def anime_list():
    page = request.args.get("page", 1, type=int)
    status_filter = request.args.get("status", "all").strip().lower()
    allowed_statuses = {"all", "airing", "finished", "upcoming"}

    if status_filter not in allowed_statuses:
        status_filter = "all"
    if page < 1:
        page = 1

    per_page = 24
    offset = (page - 1) * per_page

    season_key, season_year, season_label, (start_month, end_month) = current_season_info()
    db = get_db()

    season_condition = """
        (
            (anime.season = ? AND anime.year = ?)
            OR
            (anime.start_year = ? AND anime.start_month BETWEEN ? AND ?)
        )
    """
    params = [
        season_key,
        season_year,
        season_year,
        start_month,
        end_month,
    ]

    status_sql = ""
    if status_filter != "all":
        status_sql = " AND anime.status = ?"
        params.append(status_filter)

    anime_rows = db.execute(
        f"""
        SELECT
            anime.*,
            ROUND(AVG(reviews.rating), 1) AS user_score,
            COUNT(reviews.id) AS review_count,
            COALESCE(
                ROUND(AVG(reviews.rating), 1),
                anime.mal_score
            ) AS display_score
        FROM anime
        LEFT JOIN reviews ON anime.id = reviews.anime_id
        WHERE {season_condition}{status_sql}
        GROUP BY anime.id
        ORDER BY display_score DESC, anime.start_month ASC, anime.start_day ASC, anime.id DESC
        LIMIT ? OFFSET ?
        """,
        tuple(params + [per_page, offset]),
    ).fetchall()

    total = db.execute(
        f"""
        SELECT COUNT(*)
        FROM anime
        WHERE {season_condition}{status_sql}
        """,
        tuple(params),
    ).fetchone()[0]

    status_counts_rows = db.execute(
        f"""
        SELECT anime.status, COUNT(*) AS count
        FROM anime
        WHERE {season_condition}
        GROUP BY anime.status
        """,
        (season_key, season_year, season_year, start_month, end_month),
    ).fetchall()
    status_counts = {row["status"]: row["count"] for row in status_counts_rows}

    db.close()

    total_pages = max(1, (total + per_page - 1) // per_page)

    return render_template(
        "anime_list.html",
        anime_list=anime_rows,
        page=page,
        total_pages=total_pages,
        total=total,
        status_filter=status_filter,
        status_counts=status_counts,
        season_label=season_label,
        season_year=season_year,
    )


@app.route("/library")
@login_required
def library():
    keyword = request.args.get("q", "").strip()[:200]
    status_filter = request.args.get("status", "all")
    if status_filter not in {"all", "airing", "finished", "upcoming"}:
        status_filter = "all"
    scope = "classics" if request.args.get("scope") == "classics" else "all"
    sort = request.args.get("sort", "popular")
    orders = {
        "popular": "anime.popularity DESC, display_score DESC, anime.id DESC",
        "score": "display_score DESC, review_count DESC, anime.popularity DESC",
        "newest": "COALESCE(anime.start_year, anime.year) DESC, anime.start_month DESC, anime.start_day DESC, anime.id DESC",
    }
    if sort not in orders:
        sort = "popular"
    where, params = [], []
    if scope == "classics":
        where.append("anime.is_classic = 1")
    if status_filter != "all":
        where.append("anime.status = ?")
        params.append(status_filter)
    if keyword:
        where.append("(anime.title LIKE ? OR anime.title_japanese LIKE ? OR anime.title_zh LIKE ? OR anime.genres LIKE ?)")
        params.extend([f"%{keyword}%"] * 4)
    condition = "WHERE " + " AND ".join(where) if where else ""
    db = get_db()
    total = db.execute(f"SELECT COUNT(*) FROM anime {condition}", params).fetchone()[0]
    per_page = 24
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(total_pages, max(1, request.args.get("page", 1, type=int)))
    rows = db.execute(f"""
        SELECT anime.*, ROUND(AVG(reviews.rating), 1) AS user_score,
            COUNT(reviews.id) AS review_count,
            COALESCE(ROUND(AVG(reviews.rating), 1), anime.mal_score) AS display_score
        FROM anime LEFT JOIN reviews ON anime.id = reviews.anime_id
        {condition} GROUP BY anime.id ORDER BY {orders[sort]} LIMIT ? OFFSET ?
    """, params + [per_page, (page - 1) * per_page]).fetchall()
    all_total = db.execute("SELECT COUNT(*) FROM anime").fetchone()[0]
    classic_total = db.execute("SELECT COUNT(*) FROM anime WHERE is_classic = 1").fetchone()[0]
    db.close()
    return render_template("library.html", anime_list=rows, total=total, all_total=all_total,
                           classic_total=classic_total, keyword=keyword, status_filter=status_filter,
                           scope=scope, sort=sort, page=page, total_pages=total_pages)


@app.route("/search")
@login_required
def search():
    keyword = request.args.get("q", "").strip()
    db = get_db()

    if keyword:
        results = db.execute(
            """
            SELECT
                anime.*,
                ROUND(AVG(reviews.rating), 1) AS user_score,
                COUNT(reviews.id) AS review_count,
                COALESCE(
                    ROUND(AVG(reviews.rating), 1),
                    anime.mal_score
                ) AS display_score
            FROM anime
            LEFT JOIN reviews ON anime.id = reviews.anime_id
            WHERE
                anime.title LIKE ?
                OR anime.title_japanese LIKE ?
                OR anime.title_zh LIKE ?
                OR anime.genres LIKE ?
            GROUP BY anime.id
            ORDER BY display_score DESC, anime.title ASC
            """,
            (
                f"%{keyword}%",
                f"%{keyword}%",
                f"%{keyword}%",
                f"%{keyword}%",
            ),
        ).fetchall()
    else:
        results = []

    db.close()

    return render_template(
        "search.html",
        results=results,
        keyword=keyword,
    )


@app.route("/monthly")
@app.route("/calendar")
@login_required
def monthly_anime():
    now = datetime.now(SITE_TIMEZONE)

    year = request.args.get("year", now.year, type=int)
    month = request.args.get("month", now.month, type=int)
    status_filter = request.args.get("status", "all").strip().lower()
    allowed_statuses = {"all", "airing", "finished", "upcoming"}

    if month < 1 or month > 12:
        month = now.month
    if status_filter not in allowed_statuses:
        status_filter = "all"

    db = get_db()

    params = [year, month]
    status_sql = ""
    if status_filter != "all":
        status_sql = " AND anime.status = ?"
        params.append(status_filter)

    anime_rows = db.execute(
        f"""
        SELECT
            anime.*,
            ROUND(AVG(reviews.rating), 1) AS user_score,
            COUNT(reviews.id) AS review_count,
            COALESCE(
                ROUND(AVG(reviews.rating), 1),
                anime.mal_score
            ) AS display_score
        FROM anime
        LEFT JOIN reviews ON anime.id = reviews.anime_id
        WHERE anime.start_year = ?
          AND anime.start_month = ?
          {status_sql}
        GROUP BY anime.id
        ORDER BY
            CASE WHEN anime.start_day IS NULL THEN 1 ELSE 0 END,
            anime.start_day ASC,
            display_score DESC,
            anime.title ASC
        """,
        tuple(params),
    ).fetchall()

    status_counts_rows = db.execute(
        """
        SELECT status, COUNT(*) AS count
        FROM anime
        WHERE start_year = ? AND start_month = ?
        GROUP BY status
        """,
        (year, month),
    ).fetchall()
    status_counts = {row["status"]: row["count"] for row in status_counts_rows}

    db.close()

    if month == 1:
        prev_year, prev_month = year - 1, 12
    else:
        prev_year, prev_month = year, month - 1

    if month == 12:
        next_year, next_month = year + 1, 1
    else:
        next_year, next_month = year, month + 1

    return render_template(
        "monthly.html",
        anime_list=anime_rows,
        year=year,
        month=month,
        prev_year=prev_year,
        prev_month=prev_month,
        next_year=next_year,
        next_month=next_month,
        status_filter=status_filter,
        status_counts=status_counts,
    )


@app.route("/upcoming")
@login_required
def upcoming_page():
    # 旧链接兼容：新番前瞻已合并进“新番日历”，跳到下个月的 Upcoming 筛选。
    now = datetime.now(SITE_TIMEZONE)
    if now.month == 12:
        year, month = now.year + 1, 1
    else:
        year, month = now.year, now.month + 1

    return redirect(
        url_for(
            "monthly_anime",
            year=year,
            month=month,
            status="upcoming",
        )
    )


@app.route("/anime/<int:anime_id>", methods=["GET", "POST"])
@login_required
def anime_detail(anime_id):
    db = get_db()

    anime = db.execute(
        "SELECT * FROM anime WHERE id = ?",
        (anime_id,),
    ).fetchone()

    if anime is None:
        db.close()
        return "Anime not found", 404

    if request.method == "POST":
        content = request.form.get("content", "").strip()

        try:
            rating = int(request.form.get("rating", ""))
        except (TypeError, ValueError):
            db.close()
            flash("评分格式不正确。", "danger")
            return redirect(url_for("anime_detail", anime_id=anime_id))

        if rating < 1 or rating > 10:
            db.close()
            flash("评分必须为 1-10 分。", "danger")
            return redirect(url_for("anime_detail", anime_id=anime_id))

        if not content:
            db.close()
            flash("评论内容不能为空。", "danger")
            return redirect(url_for("anime_detail", anime_id=anime_id))

        image_name = None
        image = request.files.get("image")

        if image and image.filename:
            if allowed_file(image.filename):
                ext = secure_filename(image.filename).rsplit(".", 1)[1].lower()
                image_name = f"{uuid.uuid4().hex}.{ext}"
                image.save(
                    os.path.join(
                        app.config["UPLOAD_FOLDER"],
                        image_name,
                    )
                )
            else:
                db.close()
                flash("仅支持 png/jpg/jpeg/gif/webp。", "danger")
                return redirect(url_for("anime_detail", anime_id=anime_id))

        db.execute(
            """
            INSERT INTO reviews(
                user_id,
                anime_id,
                rating,
                content,
                image
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                session["user_id"],
                anime_id,
                rating,
                content,
                image_name,
            ),
        )
        db.commit()
        db.close()

        flash("评价发布成功。", "success")
        return redirect(url_for("anime_detail", anime_id=anime_id))

    reviews = db.execute(
        """
        SELECT reviews.*, users.username
        FROM reviews
        JOIN users ON users.id = reviews.user_id
        WHERE reviews.anime_id = ?
        ORDER BY reviews.created_at DESC
        """,
        (anime_id,),
    ).fetchall()

    stats = db.execute(
        """
        SELECT
            ROUND(AVG(rating), 1) AS average,
            COUNT(*) AS count
        FROM reviews
        WHERE anime_id = ?
        """,
        (anime_id,),
    ).fetchone()

    db.close()

    return render_template(
        "anime.html",
        anime=anime,
        reviews=reviews,
        stats=stats,
    )


@app.route("/forum")
@login_required
def forum():
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 15
    offset = (page - 1) * per_page

    db = get_db()

    posts = db.execute(
        """
        SELECT
            forum_posts.*,
            users.username,
            COUNT(forum_replies.id) AS reply_count
        FROM forum_posts
        JOIN users ON users.id = forum_posts.user_id
        LEFT JOIN forum_replies ON forum_replies.post_id = forum_posts.id
        GROUP BY forum_posts.id
        ORDER BY forum_posts.created_at DESC, forum_posts.id DESC
        LIMIT ? OFFSET ?
        """,
        (per_page, offset),
    ).fetchall()

    total = db.execute("SELECT COUNT(*) FROM forum_posts").fetchone()[0]
    db.close()

    total_pages = max(1, (total + per_page - 1) // per_page)

    return render_template(
        "forum.html",
        posts=posts,
        page=page,
        total=total,
        total_pages=total_pages,
    )


@app.route("/forum/new", methods=["GET", "POST"])
@login_required
def forum_new():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()

        if len(title) < 2 or len(title) > 120:
            flash("帖子标题需要 2-120 个字符。", "danger")
            return render_template("forum_new.html", title=title, content=content)

        if not content or len(content) > 5000:
            flash("正文不能为空，且不能超过 5000 个字符。", "danger")
            return render_template("forum_new.html", title=title, content=content)

        db = get_db()
        cursor = db.execute(
            """
            INSERT INTO forum_posts(user_id, title, content)
            VALUES (?, ?, ?)
            """,
            (session["user_id"], title, content),
        )
        post_id = cursor.lastrowid
        db.commit()
        db.close()

        flash("帖子发布成功。", "success")
        return redirect(url_for("forum_post", post_id=post_id))

    return render_template("forum_new.html")


@app.route("/forum/post/<int:post_id>", methods=["GET", "POST"])
@login_required
def forum_post(post_id):
    db = get_db()

    post = db.execute(
        """
        SELECT forum_posts.*, users.username
        FROM forum_posts
        JOIN users ON users.id = forum_posts.user_id
        WHERE forum_posts.id = ?
        """,
        (post_id,),
    ).fetchone()

    if not post:
        db.close()
        return "Post not found", 404

    if request.method == "POST":
        content = request.form.get("content", "").strip()

        if not content or len(content) > 3000:
            db.close()
            flash("回复不能为空，且不能超过 3000 个字符。", "danger")
            return redirect(url_for("forum_post", post_id=post_id))

        db.execute(
            """
            INSERT INTO forum_replies(post_id, user_id, content)
            VALUES (?, ?, ?)
            """,
            (post_id, session["user_id"], content),
        )
        db.commit()
        db.close()

        flash("回复成功。", "success")
        return redirect(url_for("forum_post", post_id=post_id))

    db.execute(
        "UPDATE forum_posts SET views = views + 1 WHERE id = ?",
        (post_id,),
    )
    db.commit()

    replies = db.execute(
        """
        SELECT forum_replies.*, users.username
        FROM forum_replies
        JOIN users ON users.id = forum_replies.user_id
        WHERE forum_replies.post_id = ?
        ORDER BY forum_replies.created_at ASC, forum_replies.id ASC
        """,
        (post_id,),
    ).fetchall()

    db.close()

    return render_template(
        "forum_post.html",
        post=post,
        replies=replies,
    )


@app.route("/uploads/<filename>")
@login_required
def uploaded_file(filename):
    return send_from_directory(
        app.config["UPLOAD_FOLDER"],
        filename,
    )


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=os.environ.get("FLASK_DEBUG", "0") == "1",
    )
