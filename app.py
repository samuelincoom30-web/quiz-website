import os
import re
import uuid
from io import BytesIO
from datetime import datetime, timedelta

import psycopg
import fitz

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    send_file,
)

from psycopg.rows import dict_row


# ============================================================
# APP CONFIGURATION
# ============================================================

app = Flask(__name__)

app.secret_key = os.environ.get(
    "SECRET_KEY",
    "dev-secret-key-change-this"
)

DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set. Please check your environment variables."
    )


UPLOAD_FOLDER = "uploads"

os.makedirs(
    UPLOAD_FOLDER,
    exist_ok=True
)


# ============================================================
# DATABASE CONNECTION
# ============================================================

def get_db():

    return psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row,
        connect_timeout=10
    )
# ============================================================
# UNREAD MESSAGE NOTIFICATIONS
# ============================================================

@app.context_processor
def inject_unread_message_count():

    unread_message_count = 0
    user_id = session.get("user_id")

    if user_id:
        conn = None

        try:
            conn = get_db()

            result = conn.execute("""
                SELECT COUNT(*) AS unread_count
                FROM messages
                WHERE receiver_id = %s
                  AND is_read = FALSE
            """, (user_id,)).fetchone()

            if result:
                unread_message_count = result["unread_count"]

        except Exception:
            unread_message_count = 0

        finally:
            if conn:
                conn.close()

    return {
        "unread_message_count": unread_message_count
    }

# ============================================================
# DATABASE INITIALIZATION
# ============================================================

def init_db():

    conn = get_db()

    try:

        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (

                id SERIAL PRIMARY KEY,

                username VARCHAR(100) UNIQUE NOT NULL,

                password VARCHAR(255) NOT NULL,

                role VARCHAR(20) NOT NULL,

                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP

            )
        """)


        conn.execute("""
            CREATE TABLE IF NOT EXISTS quizzes (

                id SERIAL PRIMARY KEY,

                teacher_id INTEGER
                    REFERENCES users(id)
                    ON DELETE CASCADE,

                title VARCHAR(255) NOT NULL,

                description TEXT,

                published BOOLEAN DEFAULT FALSE,

                duration_minutes INTEGER DEFAULT 30,

                max_attempts INTEGER DEFAULT 1,

                randomize_questions BOOLEAN DEFAULT FALSE,

                randomize_options BOOLEAN DEFAULT FALSE,

                show_result_immediately BOOLEAN DEFAULT TRUE,

                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP

            )
        """)


        conn.execute("""
            CREATE TABLE IF NOT EXISTS questions (

                id SERIAL PRIMARY KEY,

                quiz_id INTEGER
                    REFERENCES quizzes(id)
                    ON DELETE CASCADE,

                question TEXT NOT NULL,

                option_a TEXT NOT NULL,

                option_b TEXT NOT NULL,

                option_c TEXT NOT NULL,

                option_d TEXT NOT NULL,

                correct_answer VARCHAR(1) NOT NULL

            )
        """)


        conn.execute("""
            CREATE TABLE IF NOT EXISTS attempts (

                id SERIAL PRIMARY KEY,

                quiz_id INTEGER
                    REFERENCES quizzes(id)
                    ON DELETE CASCADE,

                student_id INTEGER
                    REFERENCES users(id)
                    ON DELETE CASCADE,

                score INTEGER DEFAULT 0,

                total INTEGER DEFAULT 0,

                submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP

            )
        """)


        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (

                id SERIAL PRIMARY KEY,

                sender_id INTEGER
                    REFERENCES users(id)
                    ON DELETE CASCADE,

                receiver_id INTEGER
                    REFERENCES users(id)
                    ON DELETE CASCADE,

                subject VARCHAR(255),

                message TEXT,

                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                is_read BOOLEAN DEFAULT FALSE

            )
        """)
    
        conn.execute("""
        CREATE TABLE IF NOT EXISTS attempt_answers (

            id SERIAL PRIMARY KEY,

            attempt_id INTEGER
                REFERENCES attempts(id)
                ON DELETE CASCADE,

            question_id INTEGER
                REFERENCES questions(id)
                ON DELETE SET NULL,

            question_text TEXT NOT NULL,

            selected_answer TEXT,

            correct_answer TEXT NOT NULL,

            is_correct BOOLEAN DEFAULT FALSE

        )
    """)



        # ----------------------------------------------------
        # Make sure older databases also have these columns
        # ----------------------------------------------------

        conn.execute("""
            ALTER TABLE quizzes
            ADD COLUMN IF NOT EXISTS duration_minutes
            INTEGER DEFAULT 30
        """)

        conn.execute("""
            ALTER TABLE quizzes
            ADD COLUMN IF NOT EXISTS max_attempts
            INTEGER DEFAULT 1
        """)

        conn.execute("""
            ALTER TABLE quizzes
            ADD COLUMN IF NOT EXISTS randomize_questions
            BOOLEAN DEFAULT FALSE
        """)

        conn.execute("""
            ALTER TABLE quizzes
            ADD COLUMN IF NOT EXISTS randomize_options
            BOOLEAN DEFAULT FALSE
        """)

        conn.execute("""
            ALTER TABLE quizzes
            ADD COLUMN IF NOT EXISTS show_result_immediately
            BOOLEAN DEFAULT TRUE
        """)

        conn.execute("""
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS is_admin
            BOOLEAN DEFAULT FALSE
        """)
        conn.execute("""
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS is_active
            BOOLEAN DEFAULT TRUE
        """)

        conn.execute("""
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS class_name
            VARCHAR(100) DEFAULT 'Unassigned'
        """)

        conn.execute("""
            UPDATE users
            SET class_name = 'Unassigned'
            WHERE role = 'student'
              AND (class_name IS NULL OR TRIM(class_name) = '')
        """)

        conn.commit()


    finally:

        conn.close()


# ============================================================
# LOGIN PROTECTION
# ============================================================

def teacher_required(func):

    def wrapper(*args, **kwargs):

        if "user_id" not in session:

            return redirect(
                url_for("login")
            )

        if session.get("role") != "teacher":

            flash(
                "Teacher access required."
            )

            return redirect(
                url_for("login")
            )

        return func(*args, **kwargs)

    wrapper.__name__ = func.__name__

    return wrapper
def admin_required(func):

    def wrapper(*args, **kwargs):

        if "user_id" not in session:
            return redirect(url_for("login"))

        if session.get("role") != "teacher" or not session.get("is_admin"):
            flash("Administrator access required.")
            return redirect(url_for("teacher_dashboard"))

        return func(*args, **kwargs)

    wrapper.__name__ = func.__name__

    return wrapper



def student_required(func):

    def wrapper(*args, **kwargs):

        if "user_id" not in session:
            return redirect(url_for("login"))

        if session.get("role") != "student":
            flash("Student access required.")
            return redirect(url_for("login"))

        # Recheck the account so deactivation also stops
        # an already-authenticated student session.
        conn = get_db()
        try:
            user = conn.execute("""
                SELECT role, is_active
                FROM users
                WHERE id = %s
            """, (session["user_id"],)).fetchone()
        finally:
            conn.close()

        if (
            not user
            or user["role"] != "student"
            or not user["is_active"]
        ):
            session.clear()
            flash(
                "Your account is inactive or unavailable. "
                "Please contact the administrator."
            )
            return redirect(url_for("login"))

        return func(*args, **kwargs)

    wrapper.__name__ = func.__name__
    return wrapper


# ============================================================
# HOME
# ============================================================

@app.route("/")
def home():

    if session.get("role") == "teacher":

        return redirect(
            url_for("teacher_dashboard")
        )

    if session.get("role") == "student":

        return redirect(
            url_for("student_dashboard")
        )

    return render_template(
        "index.html"
    )


# ============================================================
# LOGIN
# ============================================================

@app.route(
    "/login",
    methods=["GET", "POST"]
)
def login():

    if request.method == "POST":

        username = request.form.get(
            "username",
            ""
        ).strip()

        password = request.form.get(
            "password",
            ""
        )

        conn = get_db()

        try:

            user = conn.execute("""
                                    SELECT
                        id,
                        username,
                        password,
                        role,
                        is_admin,
                        is_active
                    FROM users
                    WHERE username = %s
                """, (
                username,
            )).fetchone()

        finally:

            conn.close()


        if not user:

            flash(
                "Invalid username or password."
            )

            return redirect(
                url_for("login")
            )


        if user["password"] != password:

            flash(
                "Invalid username or password."
            )

            return redirect(
                url_for("login")
            )

        if user["role"] == "student" and not user["is_active"]:
            flash("Your account has been deactivated. Please contact the administrator.")
            return redirect(url_for("login"))
        session["user_id"] = user["id"]

        session["username"] = user["username"]

        session["role"] = user["role"]

        session["is_admin"] = user["is_admin"]


        if user["role"] == "teacher":

            return redirect(
                url_for("teacher_dashboard")
            )

        return redirect(
            url_for("student_dashboard")
        )


    return render_template(
        "login.html"
    )


# ============================================================
# LOGOUT
# ============================================================

@app.route("/logout")
def logout():

    session.clear()

    return redirect(
        url_for("login")
    )

# ============================================================
# ADMINISTRATOR: MANAGE TEACHERS
# ============================================================

@app.route("/admin/teachers", methods=["GET", "POST"])
@admin_required
def manage_teachers():

    conn = get_db()

    try:
        if request.method == "POST":

            full_name = request.form.get(
                "full_name", ""
            ).strip()

            username = request.form.get(
                "username", ""
            ).strip()

            password = request.form.get(
                "password", ""
            )

            if not full_name or not username or not password:
                flash("Please fill in all teacher details.")
                return redirect(url_for("manage_teachers"))

            existing = conn.execute("""
                SELECT id
                FROM users
                WHERE username = %s
            """, (username,)).fetchone()

            if existing:
                flash("That username already exists.")
                return redirect(url_for("manage_teachers"))

            conn.execute("""
                INSERT INTO users
                    (full_name, username, password, role, is_admin)
                VALUES (%s, %s, %s, 'teacher', FALSE)
            """, (full_name, username, password))

            conn.commit()

            flash("Teacher account created successfully.")

            return redirect(url_for("manage_teachers"))

        teachers = conn.execute("""
            SELECT id, full_name, username
            FROM users
            WHERE role = 'teacher'
            ORDER BY id
        """).fetchall()
    finally:
        conn.close()

    return render_template(
        "manage_teachers.html",
        teachers=teachers
    )
# ============================================================
# ADMINISTRATOR: MANAGE STUDENTS
# ============================================================

@app.route("/admin/students", methods=["GET", "POST"])
@admin_required
def manage_students():
    conn = get_db()

    try:
        if request.method == "POST":
            action = request.form.get("action", "").strip()

            # The main teacher creates student accounts.
            if action == "create":
                full_name = request.form.get("full_name", "").strip()
                username = request.form.get("username", "").strip()
                password = request.form.get("password", "")
                class_name = request.form.get("class_name", "").strip()

                if not full_name or not username or not password or not class_name:
                    flash("Complete all student account fields.")
                    return redirect(url_for("manage_students"))

                if len(password) < 8:
                    flash("The student password must contain at least 8 characters.")
                    return redirect(url_for("manage_students"))

                existing = conn.execute("""
                    SELECT id FROM users WHERE username = %s
                """, (username,)).fetchone()

                if existing:
                    flash("That username is already in use.")
                    return redirect(url_for("manage_students"))

                conn.execute("""
                    INSERT INTO users
                        (full_name, username, password, role,
                         is_admin, is_active, class_name)
                    VALUES (%s, %s, %s, 'student', FALSE, TRUE, %s)
                """, (full_name, username, password, class_name))

                conn.commit()
                flash("Student account created successfully.")
                return redirect(url_for("manage_students"))

            # All other actions must refer to an existing student.
            student_id = request.form.get("student_id", "").strip()

            if not student_id.isdigit():
                flash("Invalid student account.")
                return redirect(url_for("manage_students"))

            student_id = int(student_id)

            student = conn.execute("""
                SELECT id, username
                FROM users
                WHERE id = %s AND role = 'student'
            """, (student_id,)).fetchone()

            if not student:
                flash("Student account not found.")
                return redirect(url_for("manage_students"))

            if action == "update":
                full_name = request.form.get("full_name", "").strip()
                username = request.form.get("username", "").strip()
                class_name = request.form.get("class_name", "").strip()

                if not full_name or not username or not class_name:
                    flash("Full name, username and class are required.")
                    return redirect(url_for("manage_students"))

                existing = conn.execute("""
                    SELECT id
                    FROM users
                    WHERE username = %s AND id <> %s
                """, (username, student_id)).fetchone()

                if existing:
                    flash("That username is already in use.")
                    return redirect(url_for("manage_students"))

                conn.execute("""
                    UPDATE users
                    SET full_name = %s,
                        username = %s,
                        class_name = %s
                    WHERE id = %s AND role = 'student'
                """, (full_name, username, class_name, student_id))

                conn.commit()
                flash("Student details and class updated successfully.")

            elif action == "toggle_status":
                conn.execute("""
                    UPDATE users
                    SET is_active = NOT is_active
                    WHERE id = %s AND role = 'student'
                """, (student_id,))

                conn.commit()
                flash("Student account status updated.")

            elif action == "reset_password":
                new_password = request.form.get("new_password", "")

                if len(new_password) < 8:
                    flash("The new password must contain at least 8 characters.")
                    return redirect(url_for("manage_students"))

                conn.execute("""
                    UPDATE users
                    SET password = %s
                    WHERE id = %s AND role = 'student'
                """, (new_password, student_id))

                conn.commit()
                flash("Student password reset successfully.")

            else:
                flash("Invalid management action.")

            return redirect(url_for("manage_students"))

        search = request.args.get("q", "").strip()

        students = conn.execute("""
            SELECT
                id,
                full_name,
                username,
                is_active,
                COALESCE(NULLIF(TRIM(class_name), ''), 'Unassigned') AS class_name
            FROM users
            WHERE role = 'student'
              AND (
                  full_name ILIKE %s
                  OR username ILIKE %s
                  OR class_name ILIKE %s
              )
            ORDER BY
                COALESCE(NULLIF(TRIM(class_name), ''), 'Unassigned'),
                full_name,
                username
        """, (
            f"%{search}%",
            f"%{search}%",
            f"%{search}%"
        )).fetchall()

    finally:
        conn.close()

    return render_template(
        "manage_students.html",
        students=students,
        search=search
    )
# ============================================================
# STUDENT REGISTRATION
# ============================================================
@app.route(
"/register",
methods=["GET", "POST"]
)
def register():
    flash("Student self-registration is disabled. Please contact the main teacher for an account.")
    return redirect(url_for("login"))


# ============================================================
# TEACHER DASHBOARD
# ============================================================

@app.route("/teacher/dashboard")
@teacher_required
def teacher_dashboard():

    conn = get_db()

    try:

        if session.get("is_admin"):

            quizzes = conn.execute("""
                SELECT
                    quizzes.id,
                    quizzes.title,
                    quizzes.description,
                    quizzes.published,
                    quizzes.duration_minutes,
                    quizzes.max_attempts,
                    COUNT(questions.id) AS question_count
                FROM quizzes
                LEFT JOIN questions
                    ON quizzes.id = questions.quiz_id
                GROUP BY quizzes.id
                ORDER BY quizzes.id DESC
            """).fetchall()

            student_leaderboard = conn.execute("""
                SELECT
                    users.id,
                    users.full_name,
                    users.username,
                    COUNT(attempts.id) AS quizzes_completed,
                    ROUND(
                        AVG(
                            attempts.score * 100.0
                            / NULLIF(attempts.total, 0)
                        ),
                        1
                    ) AS average_score
                FROM users
                LEFT JOIN attempts
                    ON attempts.student_id = users.id
                WHERE users.role = 'student'
                GROUP BY
                    users.id,
                    users.full_name,
                    users.username
                ORDER BY
                    average_score DESC NULLS LAST,
                    quizzes_completed DESC,
                    users.full_name ASC
            """).fetchall()

        else:

            quizzes = conn.execute("""
                SELECT
                    quizzes.id,
                    quizzes.title,
                    quizzes.description,
                    quizzes.published,
                    quizzes.duration_minutes,
                    quizzes.max_attempts,
                    COUNT(questions.id) AS question_count
                FROM quizzes
                LEFT JOIN questions
                    ON quizzes.id = questions.quiz_id
                WHERE quizzes.teacher_id = %s
                GROUP BY quizzes.id
                ORDER BY quizzes.id DESC
            """, (
                session["user_id"],
            )).fetchall()

            student_leaderboard = conn.execute("""
                SELECT
                    users.id,
                    users.full_name,
                    users.username,
                    COUNT(attempts.id) AS quizzes_completed,
                    ROUND(
                        AVG(
                            attempts.score * 100.0
                            / NULLIF(attempts.total, 0)
                        ),
                        1
                    ) AS average_score
                FROM users
                LEFT JOIN attempts
                    ON attempts.student_id = users.id
                    AND attempts.quiz_id IN (
                        SELECT id
                        FROM quizzes
                        WHERE teacher_id = %s
                    )
                WHERE users.role = 'student'
                GROUP BY
                    users.id,
                    users.full_name,
                    users.username
                ORDER BY
                    average_score DESC NULLS LAST,
                    quizzes_completed DESC,
                    users.full_name ASC
            """, (
                session["user_id"],
            )).fetchall()

        students = conn.execute("""
            SELECT COUNT(*) AS count
            FROM users
            WHERE role = 'student'
        """).fetchone()["count"]

    finally:
        conn.close()

    return render_template(
        "teacher_dashboard.html",
        quizzes=quizzes,
        students=students,
        student_count=students,
        student_leaderboard=student_leaderboard
    )


# ============================================================
# CREATE QUIZ
# ============================================================

@app.route(
    "/teacher/create-quiz",
    methods=["GET", "POST"]
)
@teacher_required
def create_quiz():

    if request.method == "POST":

        title = request.form.get(
            "title",
            ""
        ).strip()

        description = request.form.get(
            "description",
            ""
        ).strip()


        duration_minutes = request.form.get(
            "duration_minutes",
            30,
            type=int
        )

        max_attempts = request.form.get(
            "max_attempts",
            1,
            type=int
        )


        randomize_questions = (
            request.form.get(
                "randomize_questions"
            ) == "on"
        )


        randomize_options = (
            request.form.get(
                "randomize_options"
            ) == "on"
        )


        show_result_immediately = (
            request.form.get(
                "show_result_immediately"
            ) in ("on", "1")
        )


        if not title:

            flash(
                "Quiz title is required."
            )

            return redirect(
                url_for("create_quiz")
            )


        if duration_minutes <= 0:

            duration_minutes = 30


        if max_attempts <= 0:

            max_attempts = 1


        conn = get_db()

        try:

            quiz = conn.execute("""
                INSERT INTO quizzes
                (
                    teacher_id,
                    title,
                    description,
                    duration_minutes,
                    max_attempts,
                    randomize_questions,
                    randomize_options,
                    show_result_immediately,
                    published
                )
                VALUES
                (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    FALSE
                )
                RETURNING id
            """, (
                session["user_id"],
                title,
                description,
                duration_minutes,
                max_attempts,
                randomize_questions,
                randomize_options,
                show_result_immediately
            )).fetchone()


            conn.commit()


        finally:

            conn.close()


        return redirect(
            url_for(
                "upload_questions",
                quiz_id=quiz["id"]
            )
        )


    return render_template(
        "create_quiz.html"
    )


# ============================================================
# EDIT QUIZ
# ============================================================

@app.route(
    "/teacher/quiz/<int:quiz_id>/edit",
    methods=["GET", "POST"]
)
@teacher_required
def edit_quiz(quiz_id):

    conn = get_db()

    try:

        quiz = conn.execute("""
            SELECT *
            FROM quizzes
            WHERE id = %s
            AND teacher_id = %s
        """, (
            quiz_id,
            session["user_id"]
        )).fetchone()


        if not quiz:

            flash(
                "Quiz not found."
            )

            return redirect(
                url_for("teacher_dashboard")
            )


        questions = conn.execute("""
            SELECT *
            FROM questions
            WHERE quiz_id = %s
            ORDER BY id
        """, (
            quiz_id,
        )).fetchall()


        if request.method == "POST":

            title = request.form.get(
                "title",
                ""
            ).strip()

            description = request.form.get(
                "description",
                ""
            ).strip()


            duration_minutes = request.form.get(
                "duration_minutes",
                30,
                type=int
            )

            max_attempts = request.form.get(
                "max_attempts",
                1,
                type=int
            )


            randomize_questions = (
                request.form.get(
                    "randomize_questions"
                ) == "on"
            )


            randomize_options = (
                request.form.get(
                    "randomize_options"
                ) == "on"
            )


            show_result_immediately = (
                request.form.get(
                    "show_result_immediately"
                ) in ("on", "1")
            )


            if not title:

                flash(
                    "Quiz title is required."
                )

                return redirect(
                    url_for(
                "upload_questions",
                        quiz_id=quiz_id
                    )
                )


            if duration_minutes <= 0:

                duration_minutes = 30


            if max_attempts <= 0:

                max_attempts = 1


            conn.execute("""
                UPDATE quizzes

                SET
                    title = %s,
                    description = %s,
                    duration_minutes = %s,
                    max_attempts = %s,
                    randomize_questions = %s,
                    randomize_options = %s,
                    show_result_immediately = %s

                WHERE id = %s
                AND teacher_id = %s
            """, (
                title,
                description,
                duration_minutes,
                max_attempts,
                randomize_questions,
                randomize_options,
                show_result_immediately,
                quiz_id,
                session["user_id"]
            ))


            # ------------------------------------------------
            # Update every question
            # ------------------------------------------------

            for question in questions:

                question_id = question["id"]

                question_text = request.form.get(
                    f"question_{question_id}",
                    ""
                ).strip()

                option_a = request.form.get(
                    f"option_{question_id}_A",
                    ""
                ).strip()

                option_b = request.form.get(
                    f"option_{question_id}_B",
                    ""
                ).strip()

                option_c = request.form.get(
                    f"option_{question_id}_C",
                    ""
                ).strip()

                option_d = request.form.get(
                    f"option_{question_id}_D",
                    ""
                ).strip()

                correct_answer = request.form.get(
                    f"correct_{question_id}",
                    ""
                ).upper().strip()


                if correct_answer not in [
                    "A",
                    "B",
                    "C",
                    "D"
                ]:

                    correct_answer = "A"


                conn.execute("""
                    UPDATE questions

                    SET
                        question = %s,
                        option_a = %s,
                        option_b = %s,
                        option_c = %s,
                        option_d = %s,
                        correct_answer = %s

                    WHERE id = %s
                    AND quiz_id = %s
                """, (
                    question_text,
                    option_a,
                    option_b,
                    option_c,
                    option_d,
                    correct_answer,
                    question_id,
                    quiz_id
                ))


            conn.commit()


            flash(
                "Quiz updated successfully."
            )


            return redirect(
                url_for(
                "upload_questions",
                    quiz_id=quiz_id
                )
            )


    finally:

        conn.close()


    return render_template(
        "edit_quiz.html",
        quiz=quiz,
        questions=questions
    )


# ============================================================
# PUBLISH QUIZ
# ============================================================

@app.route(
    "/teacher/quiz/<int:quiz_id>/publish",
    methods=["POST"]
)
@teacher_required
def publish_quiz(quiz_id):

    conn = get_db()

    try:

        quiz = conn.execute("""
            SELECT id
            FROM quizzes

            WHERE id = %s
            AND teacher_id = %s
        """, (
            quiz_id,
            session["user_id"]
        )).fetchone()


        if not quiz:

            flash(
                "Quiz not found."
            )

            return redirect(
                url_for("teacher_dashboard")
            )


        # -----------------------------------------------
        # Make sure the quiz has questions
        # -----------------------------------------------

        question_count = conn.execute("""
            SELECT COUNT(*) AS count
            FROM questions
            WHERE quiz_id = %s
        """, (
            quiz_id,
        )).fetchone()["count"]


        if question_count == 0:

            flash(
                "Cannot publish a quiz with no questions."
            )

            return redirect(
                url_for("teacher_dashboard")
            )


        conn.execute("""
            UPDATE quizzes

            SET published = TRUE

            WHERE id = %s
            AND teacher_id = %s
        """, (
            quiz_id,
            session["user_id"]
        ))


        conn.commit()


        flash(
            "Quiz published successfully."
        )


    finally:

        conn.close()


    return redirect(
        url_for("teacher_dashboard")
    )


# ============================================================
# UNPUBLISH QUIZ
# ============================================================

@app.route(
    "/teacher/quiz/<int:quiz_id>/unpublish",
    methods=["POST"]
)
@teacher_required
def unpublish_quiz(quiz_id):

    conn = get_db()

    try:

        conn.execute("""
            UPDATE quizzes

            SET published = FALSE

            WHERE id = %s
            AND teacher_id = %s
        """, (
            quiz_id,
            session["user_id"]
        ))


        conn.commit()


        flash(
            "Quiz unpublished successfully."
        )


    finally:

        conn.close()


    return redirect(
        url_for("teacher_dashboard")
    )


# ============================================================
# DELETE QUIZ
# ============================================================

@app.route("/teacher/quiz/<int:quiz_id>/delete", methods=["POST"])
@teacher_required
def delete_quiz(quiz_id):
    conn = get_db()

    try:
        quiz = conn.execute("""
            SELECT id, teacher_id
            FROM quizzes
            WHERE id = %s
        """, (quiz_id,)).fetchone()

        if not quiz:
            flash("Quiz not found.")
            return redirect(url_for("teacher_dashboard"))

        is_admin = bool(session.get("is_admin"))
        teacher_id = quiz["teacher_id"]

        if not is_admin and teacher_id != session["user_id"]:
            flash("You are not allowed to delete this quiz.")
            return redirect(url_for("teacher_dashboard"))

        # Preserve quizzes that have student attempts or results.
        attempt_count = conn.execute("""
            SELECT COUNT(*) AS count
            FROM attempts
            WHERE quiz_id = %s
        """, (quiz_id,)).fetchone()["count"]

        if attempt_count > 0:
            flash(
                "This quiz has student attempts and cannot be "
                "permanently deleted. Unpublish it instead to "
                "preserve the students' records."
            )
            return redirect(url_for("teacher_dashboard"))

        # Remove saved answers before deleting their attempts.
        conn.execute("""
            DELETE FROM attempt_answers
            WHERE attempt_id IN (
                SELECT id
                FROM attempts
                WHERE quiz_id = %s
            )
        """, (quiz_id,))

        # Remove attempts and questions before deleting the quiz.
        conn.execute("""
            DELETE FROM attempts
            WHERE quiz_id = %s
        """, (quiz_id,))

        conn.execute("""
            DELETE FROM questions
            WHERE quiz_id = %s
        """, (quiz_id,))

        conn.execute("""
            DELETE FROM quizzes
            WHERE id = %s
        """, (quiz_id,))

        conn.commit()
        flash("Quiz deleted successfully.")

    finally:
        conn.close()

    return redirect(url_for("teacher_dashboard"))


# ============================================================
# UPLOAD QUESTIONS FROM PDF
# ============================================================

@app.route(
    "/teacher/upload-questions/<int:quiz_id>",
    methods=["GET", "POST"]
)
@teacher_required
def upload_questions(quiz_id):

    conn = get_db()

    try:

        quiz = conn.execute("""
            SELECT *
            FROM quizzes

            WHERE id = %s
            AND teacher_id = %s
        """, (
            quiz_id,
            session["user_id"]
        )).fetchone()


        if not quiz:

            flash(
                "Quiz not found."
            )

            return redirect(
                url_for("teacher_dashboard")
            )


    finally:

        conn.close()


    # ========================================================
    # STEP 1: PDF UPLOAD
    # ========================================================

    if request.method == "POST":

        pdf_file = request.files.get(
            "pdf_file"
        )


        if pdf_file and pdf_file.filename:

            filename = os.path.basename(
                pdf_file.filename
            )


            if not filename.lower().endswith(".pdf"):

                flash(
                    "Please upload a PDF file."
                )

                return redirect(
                    url_for(
                        "upload_questions",
                        quiz_id=quiz_id
                    )
                )


            unique_filename = (
                str(uuid.uuid4())
                + "_"
                + filename
            )


            filepath = os.path.join(
                UPLOAD_FOLDER,
                unique_filename
            )


            pdf_file.save(
                filepath
            )


            extracted_text = ""


            try:

                document = fitz.open(
                    filepath
                )


                for page in document:

                    extracted_text += (
                        page.get_text()
                        + "\n"
                    )


                document.close()


            except Exception as e:

                flash(
                    f"Could not read PDF: {e}"
                )

                return redirect(
                    url_for(
                        "upload_questions",
                        quiz_id=quiz_id
                    )
                )


            # ------------------------------------------------
            # Extract questions and options
            # ------------------------------------------------

            questions = []

            current_question = None

            current_option = None


            lines = extracted_text.splitlines()


            for raw_line in lines:

                line = raw_line.strip()


                if not line:

                    continue


                # Question pattern
                question_match = re.match(
                    r"^\s*(\d+)[\.\)]\s*(.+)$",
                    line
                )


                if question_match:

                    if current_question:

                        questions.append(
                            current_question
                        )


                    current_question = {

                        "question":
                            question_match.group(2).strip(),

                        "option_a": "",

                        "option_b": "",

                        "option_c": "",

                        "option_d": "",

                        "correct_answer": "A"

                    }


                    current_option = None

                    continue


                # Option A-D
                option_match = re.match(
                    r"^\s*([A-Da-d])[\.\)]\s*(.+)$",
                    line
                )


                if option_match and current_question:

                    letter = (
                        option_match.group(1)
                        .upper()
                    )

                    option_text = (
                        option_match.group(2)
                        .strip()
                    )


                    current_question[
                        f"option_{letter.lower()}"
                    ] = option_text


                    current_option = letter

                    continue


                # Continuation line
                if current_question:

                    if current_option:

                        key = (
                            f"option_"
                            f"{current_option.lower()}"
                        )

                        current_question[key] += (
                            " " + line
                        )

                    else:

                        current_question[
                            "question"
                        ] += " " + line


            if current_question:

                questions.append(
                    current_question
                )


            if not questions:

                flash(
                    "No questions could be extracted from the PDF."
                )

                return redirect(
                    url_for(
                        "upload_questions",
                        quiz_id=quiz_id
                    )
                )


            return render_template(
                "upload_questions.html",
                quiz=quiz,
                questions=questions
            )


    # ========================================================
    # STEP 2: SAVE REVIEWED QUESTIONS
    # ========================================================

    if request.method == "POST":

        # If no PDF was uploaded, the form is the
        # reviewed-question submission.

        if not request.files.get(
            "pdf_file"
        ):

            try:

                question_count = int(
                    request.form.get(
                        "question_count",
                        0
                    )
                )


            except ValueError:

                question_count = 0


            if question_count <= 0:

                flash(
                    "No questions were submitted."
                )

                return redirect(
                    url_for(
                        "upload_questions",
                        quiz_id=quiz_id
                    )
                )


            conn = get_db()

            try:

                for i in range(
                    question_count
                ):

                    question_text = request.form.get(
                        f"question_{i}",
                        ""
                    ).strip()

                    option_a = request.form.get(
                        f"option_a_{i}",
                        ""
                    ).strip()

                    option_b = request.form.get(
                        f"option_b_{i}",
                        ""
                    ).strip()

                    option_c = request.form.get(
                        f"option_c_{i}",
                        ""
                    ).strip()

                    option_d = request.form.get(
                        f"option_d_{i}",
                        ""
                    ).strip()

                    correct_answer = request.form.get(
                        f"correct_answer_{i}",
                        "A"
                    ).upper().strip()


                    if not question_text:

                        continue


                    if correct_answer not in [
                        "A",
                        "B",
                        "C",
                        "D"
                    ]:

                        correct_answer = "A"


                    conn.execute("""
                        INSERT INTO questions
                        (
                            quiz_id,
                            question,
                            option_a,
                            option_b,
                            option_c,
                            option_d,
                            correct_answer
                        )

                        VALUES
                        (
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s
                        )
                    """, (
                        quiz_id,
                        question_text,
                        option_a,
                        option_b,
                        option_c,
                        option_d,
                        correct_answer
                    ))


                conn.commit()


            finally:

                conn.close()


            flash(
                "Questions saved successfully."
            )


            return redirect(
                url_for(
                "upload_questions",
                    quiz_id=quiz_id
                )
            )


    return render_template(
        "upload_questions.html",
        quiz=quiz
    )


# ============================================================
# STUDENT DASHBOARD
# ============================================================

@app.route("/student/dashboard")
@student_required
def student_dashboard():
    conn = get_db()

    try:
        quizzes = conn.execute("""
            SELECT
                quizzes.id,
                quizzes.title,
                quizzes.description,
                quizzes.duration_minutes,
                quizzes.max_attempts,
                COUNT(questions.id) AS question_count
            FROM quizzes
            LEFT JOIN questions
                ON quizzes.id = questions.quiz_id
            WHERE quizzes.published = TRUE
            GROUP BY quizzes.id
            ORDER BY quizzes.id DESC
        """).fetchall()

        results = conn.execute("""
            SELECT
                attempts.id,
                quizzes.title,
                attempts.score,
                attempts.total,
                attempts.submitted_at
            FROM attempts
            JOIN quizzes
                ON attempts.quiz_id = quizzes.id
            WHERE attempts.student_id = %s
            ORDER BY attempts.submitted_at DESC
        """, (
            session["user_id"],
        )).fetchall()

    finally:
        conn.close()

    return render_template(
        "student_dashboard.html",
        quizzes=quizzes,
        results=results
    )
# ============================================================
# STUDENT LEADERBOARD
# ============================================================

@app.route("/student/leaderboard")
@student_required
def student_leaderboard():

    conn = get_db()

    try:
        leaderboard = conn.execute("""
            WITH student_scores AS (
                SELECT
                    users.id AS student_id,
                    users.full_name,
                    users.username,
                    AVG(
                        attempts.score * 100.0
                        / NULLIF(attempts.total, 0)
                    ) AS average_percentage,
                    COUNT(attempts.id) AS attempts_count
                FROM users
                JOIN attempts
                    ON attempts.student_id = users.id
                WHERE users.role = 'student'
                  AND attempts.total > 0
                GROUP BY
                    users.id,
                    users.full_name,
                    users.username
            ),
            ranked_students AS (
                SELECT
                    student_id,
                    full_name,
                    username,
                    average_percentage,
                    attempts_count,
                    ROW_NUMBER() OVER (
                        ORDER BY
                            average_percentage DESC,
                            attempts_count DESC,
                            full_name ASC,
                            student_id ASC
                    ) AS rank_position
                FROM student_scores
            )
            SELECT
                student_id,
                full_name,
                username,
                average_percentage,
                attempts_count,
                rank_position
            FROM ranked_students
            ORDER BY rank_position
            LIMIT 10
        """).fetchall()

        my_rank = conn.execute("""
            WITH student_scores AS (
                SELECT
                    users.id AS student_id,
                    users.full_name,
                    AVG(
                        attempts.score * 100.0
                        / NULLIF(attempts.total, 0)
                    ) AS average_percentage,
                    COUNT(attempts.id) AS attempts_count
                FROM users
                JOIN attempts
                    ON attempts.student_id = users.id
                WHERE users.role = 'student'
                  AND attempts.total > 0
                GROUP BY
                    users.id,
                    users.full_name
            ),
            ranked_students AS (
                SELECT
                    student_id,
                    full_name,
                    average_percentage,
                    attempts_count,
                    ROW_NUMBER() OVER (
                        ORDER BY
                            average_percentage DESC,
                            attempts_count DESC,
                            full_name ASC,
                            student_id ASC
                    ) AS rank_position
                FROM student_scores
            )
            SELECT
                rank_position,
                average_percentage,
                attempts_count
            FROM ranked_students
            WHERE student_id = %s
        """, (session["user_id"],)).fetchone()

    finally:
        conn.close()

    return render_template(
        "student_leaderboard.html",
        leaderboard=leaderboard,
        my_rank=my_rank
    )
# ============================================================
# STUDENT QUIZZES PAGE
# ============================================================

@app.route("/student/quizzes")
@student_required
def student_quizzes():
    conn = get_db()
    try:
        quizzes = conn.execute("""
            SELECT
                quizzes.id,
                quizzes.title,
                quizzes.description,
                quizzes.duration_minutes,
                quizzes.max_attempts,
                COUNT(questions.id) AS question_count
            FROM quizzes
            LEFT JOIN questions
                ON quizzes.id = questions.quiz_id
            WHERE quizzes.published = TRUE
            GROUP BY quizzes.id
            ORDER BY quizzes.id DESC
        """).fetchall()
    finally:
        conn.close()

    return render_template("quizzes.html", quizzes=quizzes)

# ============================================================
# TAKE QUIZ
# ============================================================

@app.route(
    "/student/quiz/<int:quiz_id>",
    methods=["GET", "POST"]
)
@student_required
def take_quiz(quiz_id):

    conn = get_db()

    try:

        quiz = conn.execute("""
            SELECT *
            FROM quizzes

            WHERE id = %s
            AND published = TRUE
        """, (
            quiz_id,
        )).fetchone()


        if not quiz:

            flash(
                "Quiz not found or is not published."
            )

            return redirect(
                url_for("student_dashboard")
            )


        questions = conn.execute("""
            SELECT *
            FROM questions

            WHERE quiz_id = %s

            ORDER BY id
        """, (
            quiz_id,
        )).fetchall()


        if not questions:

            flash(
                "This quiz has no questions."
            )

            return redirect(
                url_for("student_dashboard")
            )


        # ----------------------------------------------------
        # Count attempts
        # ----------------------------------------------------

        attempt_count = conn.execute("""
            SELECT COUNT(*) AS count
            FROM attempts

            WHERE quiz_id = %s
            AND student_id = %s
        """, (
            quiz_id,
            session["user_id"]
        )).fetchone()["count"]


        max_attempts = quiz[
            "max_attempts"
        ] or 1


        # ----------------------------------------------------
        # Check attempt limit
        # ----------------------------------------------------

        if attempt_count >= max_attempts:

            flash(
                "You have used all your allowed attempts."
            )

            return redirect(
                url_for("student_dashboard")
            )

        # ----------------------------------------------------
        # QUIZ COUNTDOWN TIMER
        # ----------------------------------------------------

        timer_key = f"quiz_start_{quiz_id}"
        attempt_key = f"quiz_attempt_count_{quiz_id}"

        duration_seconds = max(
            60,
            int(quiz["duration_minutes"] or 30) * 60
        )

        timer_was_valid = (
            timer_key in session
            and session.get(attempt_key) == attempt_count
        )

        if request.method == "GET":

            # Start a fresh timer when there is no timer for
            # this quiz or the completed-attempt count changed.
            if not timer_was_valid:
                session[timer_key] = (
                    datetime.now().timestamp()
                )

                session[attempt_key] = attempt_count

        elif not timer_was_valid:

            # A POST without an active timer is expired.
            session[timer_key] = (
                datetime.now().timestamp() - duration_seconds
            )

            session[attempt_key] = attempt_count

        start_time = float(session[timer_key])

        elapsed_seconds = max(
            0,
            datetime.now().timestamp() - start_time
        )

        remaining_seconds = max(
            0,
            int(duration_seconds - elapsed_seconds)
        )

        # ----------------------------------------------------
        # ENFORCE THE SERVER-SIDE DEADLINE
        # ----------------------------------------------------

        # Allow a short grace period for automatic browser
        # submission and network delay.
        submission_expired = (
            request.method == "POST"
            and (
                not timer_was_valid
                or elapsed_seconds > duration_seconds + 10
            )
        )

        if submission_expired:
            # Record the expired attempt with zero marks so
            # it cannot be repeatedly retried to bypass the
            # configured attempt limit.
            conn.execute("""
                INSERT INTO attempts
                (
                    quiz_id,
                    student_id,
                    score,
                    total
                )
                VALUES (%s, %s, %s, %s)
            """, (
                quiz_id,
                session["user_id"],
                0,
                len(questions)
            ))

            conn.commit()
            session.pop(timer_key, None)
            session.pop(attempt_key, None)

            flash(
                "Time expired. This attempt was recorded with a score of zero."
            )
            return redirect(url_for("student_dashboard"))

        # ----------------------------------------------------
        # SUBMIT QUIZ
        # ----------------------------------------------------

        if request.method == "POST":

            score = 0
            total = len(questions)
            answer_records = []

            for question in questions:
                selected_answer = request.form.get(
                    f"question_{question['id']}"
                )

                correct_answer = question["correct_answer"]
                is_correct = selected_answer == correct_answer

                if is_correct:
                    score += 1

                selected_option_text = (
                    question[f"option_{selected_answer.lower()}"]
                    if selected_answer in ("A", "B", "C", "D")
                    else None
                )

                correct_option_text = (
                    question[f"option_{correct_answer.lower()}"]
                    if correct_answer in ("A", "B", "C", "D")
                    else None
                )

                answer_records.append((
                    question["id"],
                    question["question"],
                    selected_answer,
                    correct_answer,
                    is_correct,
                    selected_option_text,
                    correct_option_text
                ))

            attempt = conn.execute("""
                    INSERT INTO attempts
                    (
                        quiz_id,
                        student_id,
                        score,
                        total
                    )
                    VALUES (%s, %s, %s, %s)
                    RETURNING id
                """, (
                quiz_id,
                session["user_id"],
                score,
                total
            )).fetchone()

            attempt_id = attempt["id"]

            for answer in answer_records:
                conn.execute("""
                        INSERT INTO attempt_answers
                        (
                            attempt_id,
                            question_id,
                            question_text,
                            selected_answer,
                            correct_answer,
                            is_correct
                        )
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (
                    attempt_id,
                    answer[0],
                    answer[1],
                    answer[2],
                    answer[3],
                    answer[4]
                ))

            conn.commit()
            # Clear the completed attempt's timer.
            session.pop(timer_key, None)
            session.pop(attempt_key, None)

            if quiz["show_result_immediately"]:
                return render_template(
                    "quiz_result.html",
                    quiz=quiz,
                    score=score,
                    total=total,
                    answer_records=answer_records
                )

            flash("Quiz submitted successfully.")
            return redirect(url_for("student_dashboard"))

    finally:

        conn.close()


    return render_template(
        "take_quiz.html",
        quiz=quiz,
        questions=questions,
        remaining_seconds=remaining_seconds
    )


# ============================================================
# STUDENT RESULTS
# ============================================================

@app.route("/student/results")
@student_required
def student_results():

    conn = get_db()

    try:

        results = conn.execute("""
            SELECT
                attempts.id,
                quizzes.title,
                attempts.score,
                attempts.total,
                attempts.submitted_at
            FROM attempts

            JOIN quizzes
                ON attempts.quiz_id = quizzes.id

            WHERE attempts.student_id = %s

            ORDER BY attempts.submitted_at DESC
        """, (
            session["user_id"],
        )).fetchall()


    finally:

        conn.close()


    return render_template(
        "student_results.html",
        results=results
    )

# ============================================================
# TEACHER RESULTS
# ============================================================

@app.route("/teacher/results")
@teacher_required
def teacher_results():

    quiz_id = request.args.get("quiz_id", type=int)

    conn = get_db()

    try:

        if session.get("is_admin"):

            if quiz_id:
                results = conn.execute("""
                    SELECT
                        attempts.id,
                        quizzes.title,
                        users.full_name,
                        users.username,
                        attempts.score,
                        attempts.total,
                        attempts.submitted_at
                    FROM attempts
                    JOIN quizzes
                        ON attempts.quiz_id = quizzes.id
                    JOIN users
                        ON attempts.student_id = users.id
                    WHERE quizzes.id = %s
                    ORDER BY attempts.submitted_at DESC
                """, (quiz_id,)).fetchall()

            else:
                results = conn.execute("""
                    SELECT
                        attempts.id,
                        quizzes.title,
                        users.full_name,
                        users.username,
                        attempts.score,
                        attempts.total,
                        attempts.submitted_at
                    FROM attempts
                    JOIN quizzes
                        ON attempts.quiz_id = quizzes.id
                    JOIN users
                        ON attempts.student_id = users.id
                    ORDER BY attempts.submitted_at DESC
                """).fetchall()

        else:

            if quiz_id:
                results = conn.execute("""
                    SELECT
                        attempts.id,
                        quizzes.title,
                        users.full_name,
                        users.username,
                        attempts.score,
                        attempts.total,
                        attempts.submitted_at
                    FROM attempts
                    JOIN quizzes
                        ON attempts.quiz_id = quizzes.id
                    JOIN users
                        ON attempts.student_id = users.id
                    WHERE quizzes.teacher_id = %s
                      AND quizzes.id = %s
                    ORDER BY attempts.submitted_at DESC
                """, (
                    session["user_id"],
                    quiz_id
                )).fetchall()

            else:
                results = conn.execute("""
                    SELECT
                        attempts.id,
                        quizzes.title,
                        users.full_name,
                        users.username,
                        attempts.score,
                        attempts.total,
                        attempts.submitted_at
                    FROM attempts
                    JOIN quizzes
                        ON attempts.quiz_id = quizzes.id
                    JOIN users
                        ON attempts.student_id = users.id
                    WHERE quizzes.teacher_id = %s
                    ORDER BY attempts.submitted_at DESC
                """, (
                    session["user_id"],
                )).fetchall()

    finally:
        conn.close()

    return render_template(
        "teacher_results.html",
        results=results,
        quiz_id=quiz_id
    )
# ============================================================
# DOWNLOAD STUDENT QUIZ REPORT AS PDF
# ============================================================

@app.route("/teacher/attempt/<int:attempt_id>/report.pdf")
@teacher_required
def download_attempt_report(attempt_id):

    conn = get_db()

    try:
        # Verify that this attempt belongs to a quiz
        # owned by the logged-in teacher.
        attempt = conn.execute("""
            SELECT
                attempts.id,
                attempts.score,
                attempts.total,
                attempts.submitted_at,
                quizzes.title AS quiz_title,
                users.full_name,
                users.username

            FROM attempts

            JOIN quizzes
                ON attempts.quiz_id = quizzes.id

            JOIN users
                ON attempts.student_id = users.id

            WHERE attempts.id = %s
            AND quizzes.teacher_id = %s
        """, (
            attempt_id,
            session["user_id"]
        )).fetchone()

        if not attempt:
            flash("Report not found or access denied.")
            return redirect(url_for("teacher_results"))

        # Retrieve the answers saved when the student
        # submitted the quiz.
        answers = conn.execute("""
            SELECT
                attempt_answers.question_text,
                attempt_answers.selected_answer,
                attempt_answers.correct_answer,
                attempt_answers.is_correct,
                questions.option_a,
                questions.option_b,
                questions.option_c,
                questions.option_d

            FROM attempt_answers

            LEFT JOIN questions
                ON attempt_answers.question_id = questions.id

            WHERE attempt_answers.attempt_id = %s

            ORDER BY attempt_answers.id
        """, (
            attempt_id,
        )).fetchall()

    finally:
        conn.close()

    # Build the PDF in memory.
    pdf_buffer = BytesIO()

    document = SimpleDocTemplate(
        pdf_buffer,
        pagesize=A4,
        rightMargin=15 * mm,
        leftMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm
    )


    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import KeepTogether

    styles = getSampleStyleSheet()
    story = []

    from xml.sax.saxutils import escape

    # --------------------------------------------------
    # PROFESSIONAL PDF COLOURS
    # --------------------------------------------------
    navy = colors.HexColor("#163A5F")
    blue = colors.HexColor("#245B85")
    light_blue = colors.HexColor("#EAF2F8")
    green = colors.HexColor("#18794E")
    light_green = colors.HexColor("#E8F5E9")
    red = colors.HexColor("#B42318")
    light_red = colors.HexColor("#FDECEC")
    grey = colors.HexColor("#64748B")
    light_grey = colors.HexColor("#F1F5F9")
    white = colors.white

    # --------------------------------------------------
    # PROFESSIONAL TEXT STYLES
    # --------------------------------------------------
    school_style = ParagraphStyle(
        "SchoolName",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=19,
        leading=23,
        textColor=navy,
        alignment=1,
        spaceAfter=4
    )

    report_title_style = ParagraphStyle(
        "ReportTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=16,
        textColor=blue,
        alignment=1,
        spaceAfter=8
    )

    section_style = ParagraphStyle(
        "SectionHeading",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=14,
        textColor=navy,
        spaceBefore=4,
        spaceAfter=6
    )

    question_style = ParagraphStyle(
        "QuestionText",
        parent=styles["BodyText"],
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=14,
        textColor=navy,
        spaceAfter=5
    )

    option_style = ParagraphStyle(
        "OptionText",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=9,
        leading=13,
        leftIndent=8,
        spaceAfter=3
    )

    correct_option_style = ParagraphStyle(
        "CorrectOption",
        parent=option_style,
        fontName="Helvetica-Bold",
        textColor=green
    )

    selected_option_style = ParagraphStyle(
        "SelectedOption",
        parent=option_style,
        fontName="Helvetica-Bold",
        textColor=red
    )

    detail_style = ParagraphStyle(
        "DetailText",
        parent=styles["BodyText"],
        fontSize=9,
        leading=13
    )

    small_style = ParagraphStyle(
        "SmallText",
        parent=styles["Normal"],
        fontSize=8,
        leading=11,
        textColor=grey
    )

    # --------------------------------------------------
    # SAFE TEXT AND ANSWER HELPERS
    # --------------------------------------------------
    def safe_text(value):
        if value is None or value == "":
            return "Not answered"

        return escape(str(value))

    def answer_text(letter, answer):
        if not letter:
            return "Not answered"

        letter = str(letter).strip().upper()

        options = {
            "A": answer["option_a"],
            "B": answer["option_b"],
            "C": answer["option_c"],
            "D": answer["option_d"]
        }

        option = options.get(letter)

        if option is not None:
            return f"{letter}. {safe_text(option)}"

        return safe_text(letter)

    # --------------------------------------------------
    # SCHOOL HEADING
    # --------------------------------------------------
    story.append(Spacer(1, 3 * mm))

    story.append(Paragraph(
        "STOCKWELL MONTESSORI SCHOOL",
        school_style
    ))

    story.append(Paragraph(
        "STUDENT QUIZ PERFORMANCE REPORT",
        report_title_style
    ))

    heading_line = Table(
        [[""]],
        colWidths=[180 * mm],
        rowHeights=[1.5 * mm]
    )

    heading_line.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), navy),
        ("LINEBELOW", (0, 0), (-1, -1), 0, navy),
    ]))

    story.append(heading_line)
    story.append(Spacer(1, 6 * mm))

    # --------------------------------------------------
    # STUDENT AND QUIZ INFORMATION
    # --------------------------------------------------
    score = attempt["score"] or 0
    total = attempt["total"] or 0

    percentage = (score / total * 100) if total else 0

    student_name = (
            attempt["full_name"]
            or attempt["username"]
            or "Student"
    )

    submitted_at = attempt["submitted_at"]

    if submitted_at:
        submitted_at = str(submitted_at)
    else:
        submitted_at = "Not available"

    information_data = [
        [
            Paragraph("<b>Student Name</b>", detail_style),
            Paragraph(safe_text(student_name), detail_style),
            Paragraph("<b>Username</b>", detail_style),
            Paragraph(safe_text(attempt["username"]), detail_style)
        ],
        [
            Paragraph("<b>Quiz Title</b>", detail_style),
            Paragraph(safe_text(attempt["quiz_title"]), detail_style),
            Paragraph("<b>Submitted</b>", detail_style),
            Paragraph(safe_text(submitted_at), detail_style)
        ]
    ]

    information_table = Table(
        information_data,
        colWidths=[27 * mm, 63 * mm, 25 * mm, 65 * mm]
    )

    information_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), light_blue),
        ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#CBD5E1")),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))

    story.append(information_table)
    story.append(Spacer(1, 5 * mm))

    # --------------------------------------------------
    # SCORE SUMMARY
    # --------------------------------------------------
    score_data = [[
        Paragraph(
            "<b>FINAL SCORE</b><br/>"
            f"{score} / {total}",
            ParagraphStyle(
                "ScoreBox",
                parent=detail_style,
                fontName="Helvetica-Bold",
                fontSize=13,
                leading=19,
                alignment=1,
                textColor=navy
            )
        ),
        Paragraph(
            "<b>PERCENTAGE</b><br/>"
            f"{percentage:.1f}%",
            ParagraphStyle(
                "PercentageBox",
                parent=detail_style,
                fontName="Helvetica-Bold",
                fontSize=13,
                leading=19,
                alignment=1,
                textColor=green if percentage >= 50 else red
            )
        ),
        Paragraph(
            "<b>QUESTIONS ANSWERED</b><br/>"
            f"{sum(1 for a in answers if a['selected_answer'])} / {len(answers)}",
            ParagraphStyle(
                "AnsweredBox",
                parent=detail_style,
                fontName="Helvetica-Bold",
                fontSize=12,
                leading=19,
                alignment=1,
                textColor=navy
            )
        )
    ]]

    score_table = Table(
        score_data,
        colWidths=[60 * mm, 60 * mm, 60 * mm]
    )

    score_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), light_blue),
        ("BACKGROUND", (1, 0), (1, 0), light_green),
        ("BACKGROUND", (2, 0), (2, 0), light_grey),
        ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#CBD5E1")),
        ("INNERGRID", (0, 0), (-1, -1), 0.6, white),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))

    story.append(score_table)
    story.append(Spacer(1, 7 * mm))

    story.append(Paragraph(
        "QUESTIONS AND ANSWERS",
        section_style
    ))

    # --------------------------------------------------
    # DISPLAY EACH QUESTION AND ALL FOUR OPTIONS
    # --------------------------------------------------
    if not answers:
        story.append(Paragraph(
            "No saved answer details were found for this attempt.",
            detail_style
        ))

    for index, answer in enumerate(answers, start=1):

        question_text = safe_text(answer["question_text"])

        selected_letter = (
            str(answer["selected_answer"]).strip().upper()
            if answer["selected_answer"]
            else ""
        )

        correct_letter = (
            str(answer["correct_answer"]).strip().upper()
            if answer["correct_answer"]
            else ""
        )

        is_correct = bool(answer["is_correct"])

        question_heading = Table(
            [[Paragraph(
                f"QUESTION {index}",
                ParagraphStyle(
                    f"QuestionNumber{index}",
                    parent=detail_style,
                    fontName="Helvetica-Bold",
                    fontSize=10,
                    leading=13,
                    textColor=white
                )
            )]],
            colWidths=[180 * mm]
        )

        question_heading.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), navy),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ]))

        question_content = [
            question_heading,
            Spacer(1, 3 * mm),
            Paragraph(question_text, question_style)
        ]

        option_values = {
            "A": answer["option_a"],
            "B": answer["option_b"],
            "C": answer["option_c"],
            "D": answer["option_d"]
        }

        for letter in ["A", "B", "C", "D"]:
            option_value = option_values[letter]

            if option_value is None or str(option_value).strip() == "":
                continue

            marker = ""

            if letter == correct_letter:
                marker = "  [CORRECT ANSWER]"
                option_format = correct_option_style

            elif letter == selected_letter and not is_correct:
                marker = "  [STUDENT'S ANSWER]"
                option_format = selected_option_style

            else:
                option_format = option_style

            option_text = (
                f"<b>{letter}.</b> "
                f"{safe_text(option_value)}"
                f"<b>{marker}</b>"
            )

            question_content.append(
                Paragraph(option_text, option_format)
            )

        if not any(
                option_values[letter] is not None
                and str(option_values[letter]).strip() != ""
                for letter in ["A", "B", "C", "D"]
        ):
            question_content.append(
                Paragraph(
                    "Options are not available for this question.",
                    small_style
                )
            )

        question_content.append(Spacer(1, 2 * mm))

        result_text = (
            "CORRECT"
            if is_correct
            else "INCORRECT"
        )

        result_colour = green if is_correct else red

        result_background = light_green if is_correct else light_red

        selected_text = answer_text(
            answer["selected_answer"],
            answer
        )

        correct_text = answer_text(
            answer["correct_answer"],
            answer
        )

        answer_summary = Table(
            [[
                Paragraph(
                    f"<b>Student's Answer</b><br/>{selected_text}",
                    detail_style
                ),
                Paragraph(
                    f"<b>Correct Answer</b><br/>{correct_text}",
                    detail_style
                ),
                Paragraph(
                    f"<b>RESULT</b><br/><font color='{result_colour.hexval()}'>"
                    f"<b>{result_text}</b></font>",
                    detail_style
                )
            ]],
            colWidths=[60 * mm, 70 * mm, 50 * mm]
        )

        answer_summary.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), result_background),
            ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#CBD5E1")),
            ("INNERGRID", (0, 0), (-1, -1), 0.4, white),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ]))

        question_content.append(answer_summary)
        question_content.append(Spacer(1, 6 * mm))

        story.append(KeepTogether(question_content))

    # --------------------------------------------------
    # PAGE NUMBER FOOTER
    # --------------------------------------------------
    def draw_page_number(canvas, doc):
        canvas.saveState()

        canvas.setStrokeColor(colors.HexColor("#CBD5E1"))
        canvas.setLineWidth(0.5)

        canvas.line(
            15 * mm,
            13 * mm,
            A4[0] - 15 * mm,
            13 * mm
        )

        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(grey)

        canvas.drawString(
            15 * mm,
            8 * mm,
            "STOCKWELL MONTESSORI SCHOOL | Student Quiz Report"
        )

        canvas.drawRightString(
            A4[0] - 15 * mm,
            8 * mm,
            f"Page {doc.page}"
        )

        canvas.restoreState()

    document.build(
        story,
        onFirstPage=draw_page_number,
        onLaterPages=draw_page_number
    )


    pdf_buffer.seek(0)

    return send_file(
        pdf_buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"student_quiz_report_{attempt_id}.pdf"
    )
# ============================================================
## ============================================================
# STUDENT MESSAGES
# ============================================================

@app.route("/student/messages", methods=["GET", "POST"])
@student_required
def student_messages():

    conn = get_db()

    try:
        if request.method == "POST":

            teacher_id = request.form.get(
                "receiver_id",
                type=int
            )

            subject = request.form.get(
                "subject",
                ""
            ).strip()

            message_text = request.form.get(
                "message",
                ""
            ).strip()

            if not teacher_id or not subject or not message_text:
                flash("Please complete all message fields.")
                return redirect(url_for("student_messages"))

            teacher = conn.execute("""
                SELECT id
                FROM users
                WHERE id = %s
                AND role = 'teacher'
            """, (teacher_id,)).fetchone()

            if not teacher:
                flash("Please select a valid teacher.")
                return redirect(url_for("student_messages"))

            conn.execute("""
                INSERT INTO messages (
                    sender_id,
                    receiver_id,
                    subject,
                    message
                )
                VALUES (%s, %s, %s, %s)
            """, (
                session["user_id"],
                teacher_id,
                subject,
                message_text
            ))

            conn.commit()

            flash("Your message has been sent to the teacher.")
            return redirect(url_for("student_messages"))

        teachers = conn.execute("""
            SELECT id, full_name, username
            FROM users
            WHERE role = 'teacher'
            ORDER BY full_name
        """).fetchall()
        conn.execute("""
            UPDATE messages
            SET is_read = TRUE
            WHERE receiver_id = %s
              AND is_read = FALSE
        """, (session["user_id"],))

        conn.commit()

        message_history = conn.execute("""
            SELECT
                messages.id,
                messages.subject,
                messages.message,
                messages.created_at,
                messages.is_read,
                messages.sender_id,
                messages.receiver_id,
                sender.username AS sender,
                receiver.username AS receiver
            FROM messages

            JOIN users AS sender
                ON messages.sender_id = sender.id

            JOIN users AS receiver
                ON messages.receiver_id = receiver.id

            WHERE messages.sender_id = %s
               OR messages.receiver_id = %s

            ORDER BY messages.created_at DESC
        """, (
            session["user_id"],
            session["user_id"]
        )).fetchall()

    finally:
        conn.close()

    return render_template(
        "student_messages.html",
        teachers=teachers,
        messages=message_history
    )


# ============================================================
# TEACHER MESSAGES
# ============================================================
@app.route("/teacher/messages", methods=["GET", "POST"])
@teacher_required
def teacher_messages():
    conn = get_db()

    try:
        if request.method == "POST":
            student_id = request.form.get("receiver_id", type=int)
            subject = request.form.get("subject", "").strip()
            message_text = request.form.get("message", "").strip()

            if not student_id or not subject or not message_text:
                flash("Please complete all message fields.")
                return redirect(url_for("teacher_messages"))

            student = conn.execute("""
                SELECT id
                FROM users
                WHERE id = %s AND role = 'student'
            """, (student_id,)).fetchone()

            if not student:
                flash("Please select a valid student.")
                return redirect(url_for("teacher_messages"))

            conn.execute("""
                INSERT INTO messages (
                    sender_id,
                    receiver_id,
                    subject,
                    message
                )
                VALUES (%s, %s, %s, %s)
            """, (
                session["user_id"],
                student_id,
                subject,
                message_text
            ))

            conn.commit()
            flash("Your message has been sent to the student.")
            return redirect(url_for("teacher_messages"))

        students = conn.execute("""
            SELECT id, full_name, username
            FROM users
            WHERE role = 'student'
            ORDER BY full_name
        """).fetchall()

        # Mark messages received by this teacher as read.
        conn.execute("""
            UPDATE messages
            SET is_read = TRUE
            WHERE receiver_id = %s
              AND is_read = FALSE
        """, (session["user_id"],))

        conn.commit()

        # Load messages sent and received by this teacher.
        message_history = conn.execute("""
            SELECT
                messages.id,
                messages.subject,
                messages.message,
                messages.created_at,
                messages.is_read,
                messages.sender_id,
                messages.receiver_id,
                sender.username AS sender,
                receiver.username AS receiver
            FROM messages
            JOIN users AS sender
                ON messages.sender_id = sender.id
            JOIN users AS receiver
                ON messages.receiver_id = receiver.id
            WHERE messages.sender_id = %s
               OR messages.receiver_id = %s
            ORDER BY messages.created_at DESC
        """, (
            session["user_id"],
            session["user_id"]
        )).fetchall()

    finally:
        conn.close()

    return render_template(
        "teacher_messages.html",
        students=students,
        messages=message_history
    )


# ============================================================
# MESSAGES ENTRY POINT
# ============================================================

@app.route("/messages")
def messages():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") == "student":
        return redirect(url_for("student_messages"))

    if session.get("role") == "teacher":
        return redirect(url_for("teacher_messages"))

    return redirect(url_for("login"))
# ============================================================
# RUN APPLICATION
# ============================================================

if __name__ == "__main__":

    try:

        print(
            "Initializing database..."
        )

        init_db()

        print(
            "Database ready."
        )


        print(
            "Starting Flask website..."
        )


        app.run(
            debug=True,
            use_reloader=False
        )


    except Exception as e:

        print(
            "\nDATABASE / APPLICATION ERROR:"
        )

        print(
            e
        )

        raise